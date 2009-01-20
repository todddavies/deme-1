from django.core.urlresolvers import reverse
from django import template
from django.db.models import Q
from cms.models import *
from cms import permissions
from django.utils.http import urlquote
from django.utils.html import escape, urlize
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from django.utils.timesince import timesince

register = template.Library()

###############################################################################
# Helper functions
###############################################################################

def agentcan_global_helper(context, ability, wildcard_suffix=False):
    """
    Return a boolean for whether the logged in agent has the specified global
    ability. If wildcard_suffix=True, then return True if the agent has **any**
    global ability whose first word is the specified ability.
    """
    agent = context['cur_agent']
    permission_cache = context['_permission_cache']
    if wildcard_suffix:
        global_abilities = permission_cache.global_abilities(agent)
        return any(x.startswith(ability) for x in global_abilities)
    else:
        return permission_cache.agent_can_global(agent, ability)


def agentcan_helper(context, ability, item, wildcard_suffix=False):
    """
    Return a boolean for whether the logged in agent has the specified ability.
    If wildcard_suffix=True, then return True if the agent has **any** ability
    whose first word is the specified ability.
    """
    agent = context['cur_agent']
    permission_cache = context['_permission_cache']
    if wildcard_suffix:
        abilities_for_item = permission_cache.item_abilities(agent, item)
        return any(x.startswith(ability) for x in abilities_for_item)
    else:
        return permission_cache.agent_can(agent, ability, item)


###############################################################################
# Filters and templates
###############################################################################

@register.filter
def icon_url(item_type, size=32):
    """
    Return a URL for an icon for the item type, size x size pixels.
    
    The item_type can either be a string for the name of the item type, or it
    can be a class. If nothing matches, return the generic Item icon.
    
    Special strings, such as "error" and "history", are set to specific icons.
    
    Not all sizes are available (look at static/crystal_project).
    """
    item_type_to_icon = {
        'error':                'apps/error',
        'checkbox':             'apps/clean',
        'new':                  'apps/easymoblog',
        'history':              'apps/cal',
        'subscribe':            'apps/knewsticker',
        'relationships':        'apps/proxy',
        'permissions':          'apps/ksysv',
        'edit':                 'apps/kedit',
        'trash':                'filesystems/trashcan_empty',
        Agent:                  'apps/personal',
        AuthenticationMethod:   'apps/password',
        ContactMethod:          'apps/kontact',
        CustomUrl:              'mimetypes/message',
        Comment:                'apps/filetypes',
        DemeSetting:            'apps/advancedsettings',
        Document:               'mimetypes/empty',
        DjangoTemplateDocument: 'mimetypes/html',
        EmailContactMethod:     'apps/kmail',
        Excerpt:                'mimetypes/shellscript',
        FileDocument:           'mimetypes/misc',
        Folio:                  'apps/kfm',
        GlobalRole:             'apps/lassist',
        GlobalRoleAbility:      'apps/ksysv',
        Group:                  'apps/Login Manager',
        ImageDocument:          'mimetypes/images',
        Item:                   'apps/kblackbox',
        Collection:             'filesystems/folder_blue',
        Membership:             'filesystems/folder_documents',
        Person:                 'apps/access',
        Role:                   'apps/lassist',
        RoleAbility:            'apps/ksysv',
        Site:                   'devices/nfs_unmount',
        SiteDomain:             'devices/modem',
        Subscription:           'apps/knewsticker',
        TextDocument:           'mimetypes/txt',
        Transclusion:           'apps/knotes',
        ViewerRequest:          'mimetypes/message',
    }
    if isinstance(item_type, basestring):
        model = get_model_with_name(item_type)
        if model:
            return icon_url(model, size)
    elif isinstance(item_type, Item):
        return icon_url(type(item_type), size)
    elif isinstance(item_type, type) and issubclass(item_type, Item):
        if item_type not in item_type_to_icon:
            return icon_url(item_type.__base__, size)
    else:
        item_type = Item
    icon = item_type_to_icon.get(item_type, item_type_to_icon[Item])
    return "/static/crystal_project/%dx%d/%s.png" % (size, size, icon)

@register.simple_tag
def list_results_navigator(viewer, collection, search_query, trashed, offset, limit, n_results, max_pages):
    """
    Make an HTML pagination navigator (page number links with prev and next
    links on both sides).
    
    The prev link will have class="list_results_prev". The next link will have
    class="list_results_next". Each page number link will have
    class="list_results_step". The current page number will be in a span with
    class="list_results_highlighted".
    """
    if n_results <= limit:
        return ''
    url_prefix = reverse('resource_collection', kwargs={'viewer': viewer.lower()}) + '?limit=%s&' % limit
    if search_query:
        url_prefix += 'q=%s&' % search_query
    if trashed:
        url_prefix += 'trashed=1&'
    if collection:
        url_prefix += 'collection=%s&' % collection.pk
    result = []
    # Add a prev link
    if offset > 0:
        new_offset = max(0, offset - limit)
        prev_text = _('Prev')
        link = u'<a class="list_results_prev" href="%soffset=%d">&laquo; %s</a>' % (url_prefix, new_offset, prev_text)
        result.append(link)
    # Add the page links
    for new_offset in xrange(max(0, offset - limit * max_pages), min(n_results - 1, offset + limit * max_pages), limit):
        if new_offset == offset:
            link = '<span class="list_results_highlighted">%d</span>' % (1 + new_offset / limit,)
        else:
            link = '<a class="list_results_step" href="%soffset=%d">%d</a>' % (url_prefix, new_offset, 1 + new_offset / limit)
        result.append(link)
    # Add a next link
    if offset + limit < n_results:
        new_offset = offset + limit
        next_text = _('Next')
        link = u'<a class="list_results_next" href="%soffset=%d">%s &raquo;</a>' % (url_prefix, new_offset, next_text)
        result.append(link)
    return ''.join(result)

class IfAgentCan(template.Node):
    def __init__(self, ability, ability_parameter, item, nodelist_true, nodelist_false):
        self.ability = template.Variable(ability)
        self.ability_parameter = template.Variable(ability_parameter)
        self.item = template.Variable(item)
        self.nodelist_true, self.nodelist_false = nodelist_true, nodelist_false

    def __repr__(self):
        return "<IfAgentCanNode>"

    def render(self, context):
        agent = context['cur_agent']
        try:
            item = self.item.resolve(context)
        except template.VariableDoesNotExist:
            if settings.DEBUG:
                return "[Couldn't resolve item variable]"
            else:
                return '' # Fail silently for invalid variables.
        try:
            ability = self.ability.resolve(context)
        except template.VariableDoesNotExist:
            if settings.DEBUG:
                return "[Couldn't resolve ability variable]"
            else:
                return '' # Fail silently for invalid variables.
        try:
            ability_parameter = self.ability_parameter.resolve(context)
        except template.VariableDoesNotExist:
            if settings.DEBUG:
                return "[Couldn't resolve ability_parameter variable]"
            else:
                return '' # Fail silently for invalid variables.
        ability = '%s %s' % (ability, ability_parameter) if ability_parameter else ability
        if agentcan_helper(context, ability, item):
            return self.nodelist_true.render(context)
        else:
            return self.nodelist_false.render(context)

@register.tag
def ifagentcan(parser, token):
    bits = list(token.split_contents())
    if len(bits) != 4:
        raise template.TemplateSyntaxError, "%r takes three arguments" % bits[0]
    end_tag = 'end' + bits[0]
    nodelist_true = parser.parse(('else', end_tag))
    token = parser.next_token()
    if token.contents == 'else':
        nodelist_false = parser.parse((end_tag,))
        parser.delete_first_token()
    else:
        nodelist_false = template.NodeList()
    return IfAgentCan(bits[1], bits[2], bits[3], nodelist_true, nodelist_false)

class IfAgentCanGlobal(template.Node):
    def __init__(self, ability, ability_parameter, nodelist_true, nodelist_false):
        self.ability = template.Variable(ability)
        self.ability_parameter = template.Variable(ability_parameter)
        self.nodelist_true, self.nodelist_false = nodelist_true, nodelist_false

    def __repr__(self):
        return "<IfAgentCanNode>"

    def render(self, context):
        agent = context['cur_agent']
        try:
            ability = self.ability.resolve(context)
        except template.VariableDoesNotExist:
            if settings.DEBUG:
                return "[Couldn't resolve ability variable]"
            else:
                return '' # Fail silently for invalid variables.
        try:
            ability_parameter = self.ability_parameter.resolve(context)
        except template.VariableDoesNotExist:
            if settings.DEBUG:
                return "[Couldn't resolve ability_parameter variable]"
            else:
                return '' # Fail silently for invalid variables.
        ability = '%s %s' % (ability, ability_parameter) if ability_parameter else ability
        if agentcan_global_helper(context, ability):
            return self.nodelist_true.render(context)
        else:
            return self.nodelist_false.render(context)

@register.tag
def ifagentcanglobal(parser, token):
    bits = list(token.split_contents())
    if len(bits) != 3:
        raise template.TemplateSyntaxError, "%r takes two arguments" % bits[0]
    end_tag = 'end' + bits[0]
    nodelist_true = parser.parse(('else', end_tag))
    token = parser.next_token()
    if token.contents == 'else':
        nodelist_false = parser.parse((end_tag,))
        parser.delete_first_token()
    else:
        nodelist_false = template.NodeList()
    return IfAgentCanGlobal(bits[1], bits[2], nodelist_true, nodelist_false)

# remember this includes trashed comments, which should be displayed differently after calling this
def comment_dicts_for_item(item, version_number, context, include_recursive_collection_comments):
    permission_cache = context['_permission_cache']
    comment_subclasses = [TextComment, EditComment, TrashComment, UntrashComment, AddMemberComment, RemoveMemberComment]
    comments = []
    if include_recursive_collection_comments:
        if agentcan_global_helper(context, 'do_everything'):
            recursive_filter = None
        else:
            visible_memberships = Membership.objects.filter(permission_cache.filter_items(context['cur_agent'], 'view item'))
            recursive_filter = Q(child_memberships__in=visible_memberships.values('pk').query)
        members_and_me_pks_query = Item.objects.filter(trashed=False).filter(Q(pk=item.pk) | Q(pk__in=item.all_contained_collection_members(recursive_filter).values('pk').query)).values('pk').query
        comment_pks = RecursiveCommentMembership.objects.filter(parent__in=members_and_me_pks_query).values_list('child', flat=True)
    else:
        comment_pks = RecursiveCommentMembership.objects.filter(parent=item).values_list('child', flat=True)
    if comment_pks:
        permission_cache.mass_learn(context['cur_agent'], 'view created_at', Comment.objects.filter(pk__in=comment_pks))
        permission_cache.mass_learn(context['cur_agent'], 'view creator', Comment.objects.filter(pk__in=comment_pks))
        permission_cache.mass_learn(context['cur_agent'], 'view name', Agent.objects.filter(pk__in=Comment.objects.filter(pk__in=comment_pks).values('creator_id').query))
        for comment_subclass in comment_subclasses:
            new_comments = comment_subclass.objects.filter(pk__in=comment_pks)
            related_fields = ['creator']
            if include_recursive_collection_comments:
                related_fields.extend(['item'])
            if new_comments:
                if comment_subclass in [AddMemberComment, RemoveMemberComment]:
                    permission_cache.mass_learn(context['cur_agent'], 'view membership', new_comments)
                    permission_cache.mass_learn(context['cur_agent'], 'view item', Membership.objects.filter(pk__in=new_comments.values('membership_id').query))
                    permission_cache.mass_learn(context['cur_agent'], 'view name', Item.objects.filter(pk__in=Membership.objects.filter(pk__in=new_comments.values('membership_id').query).values('item_id').query))
                    related_fields.extend(['membership', 'membership__item'])
            new_comments = new_comments.select_related(*related_fields)
            comments.extend(new_comments)
    comments.sort(key=lambda x: x.created_at)
    pk_to_comment_info = {}
    for comment in comments:
        comment_info = {'comment': comment, 'subcomments': []}
        pk_to_comment_info[comment.pk] = comment_info
    result = []
    for comment in comments:
        child = pk_to_comment_info[comment.pk]
        parent = pk_to_comment_info.get(comment.item_id)
        if parent:
            parent['subcomments'].append(child)
        else:
            result.append(child)
    return result

class EntryHeader(template.Node):
    def __init__(self, page_name):
        if page_name:
            self.page_name = template.Variable(page_name)
        else:
            self.page_name = None

    def __repr__(self):
        return "<EntryHeaderNode>"

    def render(self, context):
        if self.page_name is None:
            page_name = None
        else:
            try:
                page_name = self.page_name.resolve(context)
            except template.VariableDoesNotExist:
                if settings.DEBUG:
                    return "[Couldn't resolve page_name variable]"
                else:
                    return '' # Fail silently for invalid variables.

        item = context['item']
        version_number = item.version_number
        cur_item_type = context['_viewer'].item_type
        item_type_inheritance = []
        while issubclass(cur_item_type, Item):
            item_type_inheritance.insert(0, cur_item_type.__name__)
            cur_item_type = cur_item_type.__base__

        result = []

        history_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'history'}) + '?version=%s' % version_number
        subscribe_url = reverse('resource_collection', kwargs={'viewer': 'subscription', 'action': 'new'}) + '?item=%s' % item.pk
        relationships_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'relationships'}) + '?version=%s' % version_number
        permissions_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'permissions'})
        edit_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'edit'}) + '?version=%s' % version_number
        copy_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'copy'}) + '?version=%s' % version_number
        trash_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'trash'}) + '?redirect=%s' % urlquote(context['full_path'])
        untrash_url = reverse('resource_entry', kwargs={'viewer': item.item_type.lower(), 'noun': item.pk, 'action': 'untrash'}) + '?redirect=%s' % urlquote(context['full_path'])

        result.append('<div class="crumbs">')

        result.append('<div style="float: right; margin-bottom: 5px;">')
        result.append('<a href="%s" class="img_button"><img src="%s" /><span>History</span></a>' % (history_url, icon_url('history', 16)))
        result.append('<a href="%s" class="img_button"><img src="%s" /><span>Subscribe</span></a>' % (subscribe_url, icon_url('subscribe', 16)))
        result.append('<a href="%s" class="img_button"><img src="%s" /><span>Relationships</span></a>' % (relationships_url, icon_url('relationships', 16)))
        if agentcan_helper(context, 'do_everything', item):
            result.append('<a href="%s" class="img_button"><img src="%s" /><span>Permissions</span></a>' % (permissions_url, icon_url('permissions', 16)))
        if agentcan_helper(context, 'edit', item, wildcard_suffix=True):
            result.append('<a href="%s" class="img_button"><img src="%s" /><span>Edit</span></a>' % (edit_url, icon_url('edit', 16)))
        if agentcan_global_helper(context, 'create %s' % item.item_type):
            result.append('<a href="%s" class="img_button"><img src="%s" /><span>Copy</span></a>' % (copy_url, icon_url('copy', 16)))
        if agentcan_helper(context, 'trash', item):
            result.append("""<form style="display: inline;" method="post" enctype="multipart/form-data" action="%s" class="item_form">""" % (untrash_url if item.trashed else trash_url))
            result.append("""<a href="#" onclick="this.parentNode.submit(); return false;" class="img_button"><img src="%s" /><span>%s</span></a>""" % (icon_url('trash', 16), "Untrash" if item.trashed else "Trash"))
            result.append("""</form>""")
        result.append('</div>')

        result.append('<div style="float: left; margin-bottom: 5px; margin-top: 5px;">')
        for inherited_item_type in item_type_inheritance:
            result.append('<a href="%s" class="img_link"><img src="%s" /><span>%ss</span></a> &raquo;' % (reverse('resource_collection', kwargs={'viewer': inherited_item_type.lower()}), icon_url(inherited_item_type, 16), inherited_item_type))
        if agentcan_helper(context, 'view name', item):
            result.append('<a href="%s" class="img_link"><img src="%s" /><span>%s</span></a>' % (item.get_absolute_url(), icon_url(item.item_type, 16), escape(item.name)))
        else:
            result.append('<a href="%s" class="img_link"><img src="%s" /><span>%s</span></a>' % (item.get_absolute_url(), icon_url(item.item_type, 16), escape("%s %s" % (item.item_type, item.pk))))
        if context['specific_version']:
            result.append('&raquo; ')
            result.append('v%d' % item.version_number)
        if page_name is not None:
            result.append('&raquo; ')
            result.append(page_name)
        result.append('</div>')

        result.append('<div style="clear: both;">')
        result.append('</div>')

        result.append('</div>')

        if agentcan_helper(context, 'view created_at', item):
            created_at_text = '<span title="%s">%s ago</span>' % (item.created_at.strftime("%Y-%m-%d %H:%M:%S"), timesince(item.created_at))
        else:
            created_at_text = ''
        if agentcan_helper(context, 'view updated_at', item):
            updated_at_text = '<span title="%s">%s ago</span>' % (item.updated_at.strftime("%Y-%m-%d %H:%M:%S"), timesince(item.updated_at))
        else:
            updated_at_text = ''
        if agentcan_helper(context, 'view creator', item):
            if agentcan_helper(context, 'view name', item.creator):
                creator_text = 'by <a href="%s">%s</a>' % (item.creator.get_absolute_url(), escape(item.creator.name))
            else:
                creator_text = 'by <a href="%s">%s</a>' % (item.creator.get_absolute_url(), escape('%s %s' % (item.creator.item_type, item.creator.pk)))
        else:
            creator_text = ''
        if agentcan_helper(context, 'view updater', item):
            if agentcan_helper(context, 'view name', item.updater):
                updater_text = 'by <a href="%s">%s</a>' % (item.updater.get_absolute_url(), escape(item.updater.name))
            else:
                updater_text = 'by <a href="%s">%s</a>' % (item.updater.get_absolute_url(), escape('%s %s' % (item.updater.item_type, item.updater.pk)))
        else:
            updater_text = ''
        result.append('<div style="font-size: 8pt;">')
        if creator_text or created_at_text:
            result.append('<div style="float: left;">')
            result.append('Originally created %s %s' % (creator_text, created_at_text))
            result.append('</div>')
        if updater_text or updated_at_text:
            result.append('<div style="float: right;">')
            result.append('Version %s updated %s %s' % (version_number, updater_text, updated_at_text))
            result.append('</div>')
        result.append('<div style="clear: both;">')
        result.append('</div>')
        result.append('</div>')

        result.append('<div style="font-size: 8pt; color: #aaa; margin-bottom: 10px;">')
        if agentcan_helper(context, 'view description', item) and item.description.strip():
            result.append('Description: %s' % escape(item.description))
        result.append('</div>')

        if item.trashed:
            result.append('<div style="color: #c00; font-weight: bold; font-size: larger;">This item is trashed</div>')

        return '\n'.join(result)

@register.tag
def entryheader(parser, token):
    bits = list(token.split_contents())
    if len(bits) < 1 or len(bits) > 2:
        raise template.TemplateSyntaxError, "%r takes zero or one arguments" % bits[0]
    if len(bits) == 2:
        page_name = bits[1]
    else:
        page_name = None
    return EntryHeader(page_name)


class CollectionHeader(template.Node):
    def __init__(self, page_name):
        if page_name:
            self.page_name = template.Variable(page_name)
        else:
            self.page_name = None

    def __repr__(self):
        return "<CollectionHeaderNode>"

    def render(self, context):
        if self.page_name is None:
            page_name = None
        else:
            try:
                page_name = self.page_name.resolve(context)
            except template.VariableDoesNotExist:
                if settings.DEBUG:
                    return "[Couldn't resolve page_name variable]"
                else:
                    return '' # Fail silently for invalid variables.

        item_type = context['item_type']

        cur_item_type = context['_viewer'].item_type
        item_type_inheritance = []
        while issubclass(cur_item_type, Item):
            item_type_inheritance.insert(0, cur_item_type.__name__)
            cur_item_type = cur_item_type.__base__

        result = []

        new_url = reverse('resource_collection', kwargs={'viewer': item_type.lower(), 'action': "new"})

        result.append('<div class="crumbs">')
        result.append('<div style="float: right; margin-bottom: 5px;">')
        if agentcan_global_helper(context, 'create %s' % item_type):
            result.append('<a href="%s" class="img_button"><img src="%s" /><span>New %s</span></a>' % (new_url, icon_url('new', 16), item_type))
        result.append('</div>')

        result.append('<div style="float: left; margin-bottom: 5px; margin-top: 5px;">')
        for i, inherited_item_type in enumerate(item_type_inheritance):
            link = '<a href="%s" class="img_link"><img src="%s" /><span>%ss</span></a>' % (reverse('resource_collection', kwargs={'viewer': inherited_item_type.lower()}), icon_url(inherited_item_type, 16), inherited_item_type)
            if i > 0:
                link = '&raquo; %s' % link
            result.append(link)
        if page_name is not None:
            result.append('&raquo; ')
            result.append(page_name)
        result.append('</div>')

        result.append('<div style="clear: both;">')
        result.append('</div>')

        result.append('</div>')

        return '\n'.join(result)

@register.tag
def collectionheader(parser, token):
    bits = list(token.split_contents())
    if len(bits) < 1 or len(bits) > 2:
        raise template.TemplateSyntaxError, "%r takes zero or one arguments" % bits[0]
    if len(bits) == 2:
        page_name = bits[1]
    else:
        page_name = None
    return CollectionHeader(page_name)


@register.simple_tag
def display_body_with_inline_transclusions(item, is_html):
    #TODO permissions? you should be able to see any Transclusion, but maybe not the id of the comment it refers to
    #TODO don't insert these in bad places, like inside a tag <img <a href="....> />
    #TODO when you insert a transclusion in the middle of a tag like <b>hi <COMMENT></b> then it gets the style, this is bad
    if is_html:
        format = lambda text: text
    else:
        format = lambda text: urlize(escape(text)).replace('\n', '<br />')
    transclusions = Transclusion.objects.filter(from_item=item, from_item_version_number=item.version_number, trashed=False, to_item__trashed=False).order_by('-from_item_index')
    result = []
    last_i = None
    for transclusion in transclusions:
        i = transclusion.from_item_index
        result.insert(0, format(item.body[i:last_i]))
        result.insert(0, '<a href="%s" class="commentref">%s</a>' % (transclusion.to_item.get_absolute_url(), escape(transclusion.to_item.name)))
        last_i = i
    result.insert(0, format(item.body[0:last_i]))
    return ''.join(result)


class CommentBox(template.Node):
    def __init__(self):
        pass

    def __repr__(self):
        return "<CommentBoxNode>"

    def render(self, context):
        item = context['item']
        version_number = item.version_number
        full_path = context['full_path']

        result = []
        result.append("""<div class="comment_box">""")
        result.append("""<div class="comment_box_header">""")
        if agentcan_helper(context, 'comment_on', item):
            result.append("""<a href="%s?item=%s&item_version_number=%s&redirect=%s">[+] Add Comment</a>""" % (reverse('resource_collection', kwargs={'viewer': 'textcomment', 'action': 'new'}), item.pk, version_number, urlquote(full_path)))
        result.append("""</div>""")
        def add_comments_to_div(comments, nesting_level=0):
            for comment_info in comments:
                comment = comment_info['comment']
                result.append("""<div class="comment_outer%s">""" % (' comment_outer_toplevel' if nesting_level == 0 else '',))
                result.append("""<div class="comment_header">""")
                result.append("""<div style="float: right;"><a href="%s?item=%s&item_version_number=%s&redirect=%s">[+] Reply</a></div>""" % (reverse('resource_collection', kwargs={'viewer': 'textcomment', 'action': 'new'}), comment.pk, comment.version_number, urlquote(full_path)))
                if isinstance(comment, EditComment):
                    comment_name = '[Edited]'
                elif isinstance(comment, TrashComment):
                    comment_name = '[Trashed]'
                elif isinstance(comment, UntrashComment):
                    comment_name = '[Untrashed]'
                elif isinstance(comment, AddMemberComment):
                    comment_name = '[Added Member]'
                elif isinstance(comment, RemoveMemberComment):
                    comment_name = '[Removed Member]'
                else:
                    if agentcan_helper(context, 'view name', comment):
                        comment_name = escape(comment.name)
                    else:
                        comment_name = escape('%s %s' % (comment.item_type, comment.pk))
                result.append("""<a href="%s">%s</a>""" % (comment.get_absolute_url(), comment_name))
                if agentcan_helper(context, 'view creator', comment):
                    if agentcan_helper(context, 'view name', comment.creator):
                        result.append("""by <a href="%s">%s</a>""" % (comment.creator.get_absolute_url(), escape(comment.creator.name)))
                    else:
                        result.append("""by <a href="%s">%s</a>""" % (comment.creator.get_absolute_url(), escape('%s %s' % (comment.creator.item_type, comment.creator.pk))))
                if item.pk != comment.item_id and nesting_level == 0:
                    if agentcan_helper(context, 'view name', comment.item):
                        result.append('for <a href="%s">%s</a>' % (comment.item.get_absolute_url(), escape(comment.item.name)))
                    else:
                        result.append('for <a href="%s">%s</a>' % (comment.item.get_absolute_url(), escape('%s %s' % (comment.item.item_type, comment.item.pk))))
                if agentcan_helper(context, 'view created_at', comment):
                    result.append('<span title="%s">%s ago</span>' % (comment.created_at.strftime("%Y-%m-%d %H:%M:%S"), timesince(comment.created_at)))
                result.append("</div>")
                if comment.trashed:
                    comment_body = '[TRASHED]'
                else:
                    if isinstance(comment, TextComment):
                        if agentcan_helper(context, 'view body', comment):
                            comment_body = escape(comment.body).replace('\n', '<br />')
                    elif isinstance(comment, EditComment):
                        comment_body = ''
                    elif isinstance(comment, TrashComment):
                        comment_body = ''
                    elif isinstance(comment, UntrashComment):
                        comment_body = ''
                    elif isinstance(comment, AddMemberComment):
                        if agentcan_helper(context, 'view membership', comment):
                            if agentcan_helper(context, 'view item', comment.membership):
                                if agentcan_helper(context, 'view name', comment.membership.item):
                                    comment_body = '<a href="%s">%s</a> with <a href="%s">Membership %s</a>' % (comment.membership.item.get_absolute_url(), escape(comment.membership.item.name), comment.membership.get_absolute_url(), comment.membership.pk)
                                else:
                                    comment_body = '<a href="%s">Item %s</a> with <a href="%s">Membership %s</a>' % (comment.membership.item.get_absolute_url(), comment.membership.item.pk, comment.membership.get_absolute_url(), comment.membership.pk)
                            else:
                                comment_body = '<a href="%s">Membership %s</a>' % (comment.membership.get_absolute_url(), comment.membership.pk)
                        else:
                            comment_body = ''
                    elif isinstance(comment, RemoveMemberComment):
                        if agentcan_helper(context, 'view membership', comment):
                            if agentcan_helper(context, 'view item', comment.membership):
                                if agentcan_helper(context, 'view name', comment.membership.item):
                                    comment_body = '<a href="%s">%s</a> with <a href="%s">Membership %s</a>' % (comment.membership.item.get_absolute_url(), escape(comment.membership.item.name), comment.membership.get_absolute_url(), comment.membership.pk)
                                else:
                                    comment_body = '<a href="%s">Item %s</a> with <a href="%s">Membership %s</a>' % (comment.membership.item.get_absolute_url(), comment.membership.item.pk, comment.membership.get_absolute_url(), comment.membership.pk)
                            else:
                                comment_body = '<a href="%s">Membership %s</a>' % (comment.membership.get_absolute_url(), comment.membership.pk)
                        else:
                            comment_body = ''
                    else:
                        comment_body = ''
                result.append("""<div class="comment_body">%s</div>""" % comment_body)
                add_comments_to_div(comment_info['subcomments'], nesting_level + 1)
                result.append("</div>")
        comment_dicts = comment_dicts_for_item(item, version_number, context, isinstance(item, Collection))
        add_comments_to_div(comment_dicts)
        result.append("</div>")
        return '\n'.join(result)

@register.tag
def commentbox(parser, token):
    bits = list(token.split_contents())
    if len(bits) != 1:
        raise template.TemplateSyntaxError, "%r takes no arguments" % bits[0]
    return CommentBox()


class EmbeddedItem(template.Node):
    def __init__(self, viewer_name, item):
        self.viewer_name = template.Variable(viewer_name)
        self.item = template.Variable(item)

    def __repr__(self):
        return "<EmbeddedItemNode>"

    def render(self, context):
        from cms.views import get_viewer_class_for_viewer_name, get_versioned_item
        viewer_name = self.viewer_name.resolve(context)
        viewer_class = get_viewer_class_for_viewer_name(viewer_name)
        if viewer_class is None:
            return ''
        item = self.item.resolve(context)
        if isinstance(item, basestring):
            try:
                item = Item.objects.get(pk=item)
            except ObjectDoesNotExist:
                item = None
        if not isinstance(item, Item):
            return ''
        item = item.downcast()
        item = get_versioned_item(item, None)
        viewer = viewer_class()
        viewer.init_from_div(context['_viewer'], 'show', item)
        return """<div style="padding: 10px; border: thick solid #aaa;">%s</div>""" % viewer.dispatch().content


@register.tag
def embed(parser, token):
    bits = list(token.split_contents())
    if len(bits) != 3:
        raise template.TemplateSyntaxError, "%r takes two arguments" % bits[0]
    return EmbeddedItem(bits[1], bits[2])

