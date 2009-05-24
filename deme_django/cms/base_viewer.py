#TODO completely clean up code

"""
This module defines the abstract Viewer superclass of all item viewers.
"""

from django.core.urlresolvers import reverse
from django.http import HttpResponseBadRequest, HttpResponseNotFound, HttpRequest, QueryDict
from django.utils import datastructures
from django.template import Context, loader
from django.core.exceptions import ObjectDoesNotExist
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.db import models
from django import forms
import datetime
from cms.permissions import PermissionCache
from cms.models import *
from cms.forms import JavaScriptSpamDetectionField, AjaxModelChoiceField

#TODO obey self.format when rendering an error if possible

class DemePermissionDenied(Exception):
    "The agent does not have permission to perform the action"
    pass

def get_logged_in_agent(request):
    """
    Return the currently logged in Agent (based on the cur_agent_id parameter
    in request.session), or return the AnonymousAgent if the cur_agent_id is
    missing or invalid.
    
    Also update last_online_at for the resulting Agent to the current time.
    """
    cur_agent_id = request.session.get('cur_agent_id', None)
    cur_agent = None
    if cur_agent_id is not None:
        try:
            cur_agent = Agent.objects.get(active=True, pk=cur_agent_id).downcast()
        except ObjectDoesNotExist:
            if 'cur_agent_id' in request.session:
                del request.session['cur_agent_id']
    if not cur_agent:
        try:
            cur_agent = AnonymousAgent.objects.filter(active=True)[0:1].get()
        except ObjectDoesNotExist:
            raise Exception("You must create an anonymous agent")
    Agent.objects.filter(pk=cur_agent.pk).update(last_online_at=datetime.datetime.now())
    return cur_agent


def get_default_site():
    """
    Return the default site, or raise an Exception if there is none.
    """
    try:
        return Site.objects.get(pk=DemeSetting.get('cms.default_site'))
    except ObjectDoesNotExist:
        raise Exception("You must create a default Site")

def get_current_site(request):
    """
    Return the Site that corresponds to the URL in the request, or return the
    default site (based on the DemeSetting cms.default_site) if no Site
    matches.
    """
    hostname = request.get_host().split(':')[0]
    try:
        return Site.objects.filter(hostname=hostname).get()
    except ObjectDoesNotExist:
        return get_default_site()


class VirtualRequest(HttpRequest):

    def __init__(self, original_request, path, query_string):
        self.original_request = original_request
        self.path = path
        self.query_string = query_string
        self.encoding = self.original_request.encoding
        self.GET = QueryDict(self.query_string, encoding=self._encoding)
        self.POST = QueryDict('', encoding=self._encoding)
        self.FILES = QueryDict('', encoding=self._encoding)
        self.REQUEST = datastructures.MergeDict(self.POST, self.GET)
        self.COOKIES = self.original_request.COOKIES
        self.META = {}
        self.raw_post_data = ''
        #TODO: will we ever need META, path_info, or script_name?
        self.method = 'GET'

    def get_full_path(self):
        return '%s%s' % (self.path, self.query_string and ('?' + self.query_string) or '')

    def is_secure(self):
        return self.original_request.is_secure()

    def virtual_requests_too_deep(self, n):
        if n <= 1:
            return True
        elif not hasattr(self.original_request, 'virtual_requests_too_deep'):
            return False
        else:
            return self.original_request.virtual_requests_too_deep(n - 1)


class ViewerMetaClass(type):
    """
    Metaclass for viewers. Defines ViewerMetaClass.viewer_name_dict, a mapping
    from viewer names to viewer classes.
    """
    viewer_name_dict = {}
    def __new__(cls, name, bases, attrs):
        result = super(ViewerMetaClass, cls).__new__(cls, name, bases, attrs)
        if name != 'Viewer':
            ViewerMetaClass.viewer_name_dict[attrs['viewer_name']] = result
        return result


MAXIMUM_VIRTUAL_REQUEST_DEPTH = 10


class Viewer(object):
    """
    Superclass of all viewers. Implements all of the common functionality
    like dispatching and convenience methods, but does not define any views.
    
    Although most viewers will want to inherit from ItemViewer, since it
    defines the basic views (like list, show, edit), some viewers may want to
    inherit from this abstract Viewer so they can ensure no views will be
    inherited.
    
    Subclasses must define the following fields:
    * `accepted_item_type`: The item type that this viewer is defined over. For
      example, if accepted_item_type == Agent, this viewer can be used to view
      Agents and all subclasses of Agents (e.g., Person), but an error will be
      rendered if a user tries to view something else (e.g., a Document) with
      this viewer.
    * `viewer_name`: The name of the viewer, which shows up in the URL. This
      should only consist of lowercase letters, in accordance with the Deme URL
      scheme.
    
    There are two types of views subclasses can define: item-specific views,
    and type-wide views. Item-specific views expect an item in the URL (the noun),
    while type-wide views do not expect a particular item.
    1. To define an item-speicfic view, define a method with the name
      `item_method_format`, where `method` is the name of the view (which shows
      up in the URL as the action), and format is the output format (which shows
      up in the URL as the format). For example, the method item_edit_html(self)
      in an agent viewer represents the item-specific view with action="edit" and
      format="html", and would respond to the URL `/item/agent/123/edit.html`.
    2. To define a type-wide view, define a method with the name
      `type_method_format`. For example, the method type_new_html(self) in an
      agent viewer represents the type-wide view with action="new" and
      format="html", and would respond to the URL `/item/agent/new.html`.
    
    View methods take no parameters (except self), since all of the details of
    the request are defined as instance variables in the viewer before
    dispatch. They must return an HttpResponse. They should take advantage of
    self.context, which is defined before the view is called.
    """
    __metaclass__ = ViewerMetaClass

    def __init__(self):
        # Nothing happens in the constructor. All of the initialization happens
        # in the init_from_* methods, based on how the viewer was loaded.
        pass

    def cur_agent_can_global(self, ability, wildcard_suffix=False):
        """
        Return whether the currently logged in agent has the given global
        ability. If wildcard_suffix=True, then return True if the agent has
        **any** global ability whose first word is the specified ability.
        """
        if wildcard_suffix:
            global_abilities = self.permission_cache.global_abilities(self.cur_agent)
            return any(x.startswith(ability) for x in global_abilities)
        else:
            return self.permission_cache.agent_can_global(self.cur_agent, ability)

    def cur_agent_can(self, ability, item, wildcard_suffix=False):
        """
        Return whether the currently logged in agent has the given item ability
        with respect to the given item. If wildcard_suffix=True, then return
        True if the agent has **any** ability whose first word is the specified
        ability.
        """
        if wildcard_suffix:
            abilities_for_item = self.permission_cache.item_abilities(self.cur_agent, item)
            return any(x.startswith(ability) for x in abilities_for_item)
        else:
            return self.permission_cache.agent_can(self.cur_agent, ability, item)

    def require_global_ability(self, ability, wildcard_suffix=False):
        """
        Raise a DemePermissionDenied exception if the current agent does not
        have the specified global ability.
        """
        #TODO put the details in the exception so we can display them
        if not self.cur_agent_can_global(ability, wildcard_suffix):
            raise DemePermissionDenied

    def require_ability(self, ability, item, wildcard_suffix=False):
        """
        Raise a DemePermissionDenied exception if the current agent does not
        have the specified ability with respect to the specified item.
        """
        #TODO put the details in the exception so we can display them
        if not self.cur_agent_can(ability, item, wildcard_suffix):
            raise DemePermissionDenied

    def render_error(self, title, body, request_class=HttpResponseBadRequest):
        """
        Return an HttpResponse (of type request_class) that displays a simple
        error page with the specified title and body.
        """
        template = loader.get_template_from_string("""
        {%% extends layout %%}
        {%% load item_tags %%}
        {%% block favicon %%}{{ "error"|icon_url:16 }}{%% endblock %%}
        {%% block title %%}<img src="{{ "error"|icon_url:24 }}" /> %s{%% endblock %%}
        {%% block content %%}%s{%% endblock content %%}
        """ % (title, body))
        return request_class(template.render(self.context))

    def init_for_http(self, request, action, noun, format):
        self.context = Context()
        self.permission_cache = PermissionCache()
        self.action = action or ('show' if noun else 'list')
        self.noun = noun
        self.format = format or 'html'
        self.method = (request.GET.get('_method', None) or request.method).upper()
        self.request = request
        self.cur_agent = get_logged_in_agent(request)
        self.cur_site = get_current_site(request)
        if self.noun is None:
            self.item = None
        else:
            try:
                self.item = Item.objects.get(pk=self.noun)
                self.item = self.item.downcast()
                version_number = self.request.GET.get('version')
                if version_number is not None:
                    self.item.copy_fields_from_version(version_number)
                self.context['specific_version'] = ('version' in self.request.GET)
            except ObjectDoesNotExist:
                self.item = None
        self.context['action'] = self.action
        self.context['item'] = self.item
        self.context['viewer_name'] = self.viewer_name
        self.context['accepted_item_type'] = self.accepted_item_type
        self.context['accepted_item_type_name'] = self.accepted_item_type._meta.verbose_name
        self.context['accepted_item_type_name_plural'] = self.accepted_item_type._meta.verbose_name_plural
        self.context['full_path'] = self.request.get_full_path()
        self.context['cur_agent'] = self.cur_agent
        self.context['cur_site'] = self.cur_site
        self.context['_viewer'] = self
        self._set_default_layout()

    def init_for_div(self, original_viewer, action, item):
        path = reverse('item_url', kwargs={'viewer': self.viewer_name, 'action': action, 'noun': item.pk})
        query_string = ''
        self.permission_cache = original_viewer.permission_cache
        self.request = VirtualRequest(original_viewer.request, path, query_string)
        self.cur_agent = original_viewer.cur_agent
        self.cur_site = original_viewer.cur_site
        self.format = 'html'
        self.method = 'GET'
        self.noun = item.pk
        self.item = item
        self.action = action
        self.context = Context()
        self.context['action'] = self.action
        self.context['item'] = self.item
        self.context['specific_version'] = False
        self.context['viewer_name'] = self.viewer_name
        self.context['accepted_item_type'] = self.accepted_item_type
        self.context['accepted_item_type_name'] = self.accepted_item_type._meta.verbose_name
        self.context['accepted_item_type_name_plural'] = self.accepted_item_type._meta.verbose_name_plural
        self.context['full_path'] = self.request.get_full_path()
        self.context['cur_agent'] = self.cur_agent
        self.context['cur_site'] = self.cur_site
        self.context['_viewer'] = self
        self.context['layout'] = 'blank.html'

    def init_for_outgoing_email(self, agent):
        self.permission_cache = PermissionCache()
        self.request = None
        self.cur_agent = agent
        self.cur_site = get_default_site()
        self.format = 'html'
        self.method = 'GET'
        self.noun = None
        self.item = None
        self.action = 'list'
        self.context = Context()
        self.context['action'] = self.action
        self.context['item'] = self.item
        self.context['specific_version'] = False
        self.context['viewer_name'] = self.viewer_name
        self.context['accepted_item_type'] = self.accepted_item_type
        self.context['accepted_item_type_name'] = self.accepted_item_type._meta.verbose_name
        self.context['accepted_item_type_name_plural'] = self.accepted_item_type._meta.verbose_name_plural
        self.context['full_path'] = '/'
        self.context['cur_agent'] = self.cur_agent
        self.context['cur_site'] = self.cur_site
        self.context['_viewer'] = self
        self.context['layout'] = 'blank.html'

    def dispatch(self):
        if hasattr(self.request, 'virtual_requests_too_deep'):
            if self.request.virtual_requests_too_deep(MAXIMUM_VIRTUAL_REQUEST_DEPTH):
                return self.render_error("Exceeded maximum recursion depth", 'The depth of embedded pages is too high.', HttpResponseNotFound)
        if self.noun is None:
            action_method = getattr(self, 'type_%s_%s' % (self.action, self.format), None)
        else:
            action_method = getattr(self, 'item_%s_%s' % (self.action, self.format), None)
        if action_method:
            if self.noun != None:
                if self.item is None:
                    return self.render_item_not_found()
                elif self.action == 'copy':
                    pass
                else:
                    if not isinstance(self.item, self.accepted_item_type):
                        return self.render_item_not_found()
            try:
                return action_method()
            except DemePermissionDenied:
                return self.render_error('Permission Denied', "You do not have permission to perform this action")
        else:
            return None

    def render_item_not_found(self):
        if self.item:
            title = "%s Not Found" % self.accepted_item_type.__name__
            body = 'You cannot view item %s in this viewer. Try viewing it in the <a href="%s">%s viewer</a>.' % (self.noun, reverse('item_url', kwargs={'viewer': self.item.item_type_string.lower(), 'noun': self.item.pk}), self.item.item_type_string)
        else:
            title = "Item Not Found"
            version = self.request.GET.get('version')
            if version is None:
                body = 'There is no item %s.' % self.noun
            else:
                body = 'There is no item %s version %s.' % (self.noun, version)
        return self.render_error(title, body, HttpResponseNotFound)

    def get_form_class_for_item_type(self, update_or_create, item_type, fields=None):
        if issubclass(item_type, DemeAccount):
            if update_or_create == 'update':
                return EditDemeAccountForm
            else:
                return NewDemeAccountForm
        # For now, this is how we prevent manual creation of TextDocumentExcerpts
        if issubclass(item_type, TextDocumentExcerpt):
            return forms.models.modelform_factory(item_type, fields=['name'])

        exclude = []
        for field in item_type._meta.fields:
            if (field.rel and field.rel.parent_link) or (update_or_create == 'update' and field.name in item_type.all_immutable_fields()):
                exclude.append(field.name)
        def formfield_callback(f):
            if isinstance(f, models.ForeignKey):
                return super(models.ForeignKey, f).formfield(queryset=f.rel.to._default_manager.complex_filter(f.rel.limit_choices_to), form_class=AjaxModelChoiceField, to_field_name=f.rel.field_name)
            else:
                return f.formfield()

        #TODO check modelform_factory to see if there are updates to this
        class Meta:
            pass
        setattr(Meta, 'model', item_type)
        setattr(Meta, 'fields', fields)
        setattr(Meta, 'exclude', exclude)
        class_name = item_type.__name__ + 'Form'
        attrs = {'Meta': Meta, 'formfield_callback': formfield_callback}
        attrs['action_summary'] = forms.CharField(label=_("Action summary"), help_text=_("Reason for %s this item" % ('editing' if update_or_create == 'update' else 'creating')), widget=forms.TextInput, required=False)
        if settings.USE_ANONYMOUS_JAVASCRIPT_SPAM_DETECTOR and self.cur_agent.is_anonymous():
            self.request.session.modified = True # We want to guarantee a cookie is given
            attrs['nospam'] = JavaScriptSpamDetectionField(self.request.session.session_key)
        form_class = forms.models.ModelFormMetaclass(class_name, (forms.models.ModelForm,), attrs)
        return form_class

    def _set_default_layout(self):
        cur_node = self.cur_site.default_layout
        while cur_node is not None:
            next_node = cur_node.layout
            if next_node is None:
                if cur_node.override_default_layout:
                    extends_string = ''
                else:
                    extends_string = "{% extends 'default_layout.html' %}\n"
            else:
                extends_string = "{%% extends layout%s %%}\n" % next_node.pk
            if self.permission_cache.agent_can(self.cur_agent, 'view body', cur_node):
                template_string = extends_string + cur_node.body
            else:
                template_string = "{% extends 'default_layout.html' %}\n"
                self.context['layout_permissions_problem'] = True
                next_node = None
            t = loader.get_template_from_string(template_string)
            self.context['layout%d' % cur_node.pk] = t
            cur_node = next_node
        if self.cur_site.default_layout:
            self.context['layout'] = self.context['layout%s' % self.cur_site.default_layout.pk]
        else:
            self.context['layout'] = 'default_layout.html'


def get_viewer_class_by_name(viewer_name):
    """
    Return the viewer class with the given name. If no such class exists, see
    if an item type exists with that same name (case insensitive), and if so,
    dynamically define a viewer for that item type. Otherwise, return None.
    """
    # Check the defined viewers in ViewerMetaClass.viewer_name_dict
    result = ViewerMetaClass.viewer_name_dict.get(viewer_name, None)
    if result is not None:
        return result
    # If the viewer isn't defined, see if there is an item type with the same name
    item_type = get_item_type_with_name(viewer_name, case_sensitive=False)
    if item_type is None:
        return None
    # Define an empty viewer dynamically (inheriting from a defined viewer)
    parent_item_type_with_viewer = item_type
    while issubclass(parent_item_type_with_viewer, Item):
        parent_viewer_class = ViewerMetaClass.viewer_name_dict.get(parent_item_type_with_viewer.__name__.lower(), None)
        if parent_viewer_class:
            break
        parent_item_type_with_viewer = parent_item_type_with_viewer.__base__
    if parent_viewer_class:
        class_name = '%sViewer' % item_type.__name__
        bases = (parent_viewer_class,)
        attrs = {'accepted_item_type': item_type, 'viewer_name': viewer_name}
        result = ViewerMetaClass.__new__(ViewerMetaClass, class_name, bases, attrs)
        return result
    else:
        return None
