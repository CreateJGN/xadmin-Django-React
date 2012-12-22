from functools import partial

from django.contrib.contenttypes.generic import GenericInlineModelAdmin, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import models
from django.forms.models import model_to_dict
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils.encoding import force_unicode
from django.utils.safestring import mark_safe
from django.utils.text import capfirst
from django.utils.translation import ugettext as _

from exadmin.sites import site
from exadmin.util import unquote, quote, model_format_dict
from exadmin.views import BaseAdminPlugin, ModelAdminView, CreateAdminView, UpdateAdminView, ModelFormAdminView, DeleteAdminView, ListAdminView
from exadmin.views.base import csrf_protect_m, filter_hook
from reversion.models import Revision, Version
from reversion.revisions import default_revision_manager, RegistrationError

from exadmin.plugins.inline import InlineModelAdmin
from exadmin.plugins.actions import BaseActionView

def _autoregister(admin, model, follow=None):
    """Registers a model with reversion, if required."""
    if model._meta.proxy:
        raise RegistrationError("Proxy models cannot be used with django-reversion, register the parent class instead")
    if not admin.revision_manager.is_registered(model):
        follow = follow or []
        for parent_cls, field in model._meta.parents.items():
            follow.append(field.name)
            _autoregister(admin, parent_cls)
        admin.revision_manager.register(model, follow=follow, format=admin.reversion_format)

def _registe_model(admin, model):
    if not hasattr(admin, 'revision_manager'):
        admin.revision_manager = default_revision_manager
    if not hasattr(admin, 'reversion_format'):
        admin.reversion_format = 'json'

    if not admin.revision_manager.is_registered(model):
        inline_fields = []
        for inline in getattr(admin, '_inlines', []):
            inline_model = inline.model
            if issubclass(inline, GenericInlineModelAdmin):
                ct_field = inline.ct_field
                ct_fk_field = inline.ct_fk_field
                for field in model._meta.many_to_many:
                    if isinstance(field, GenericRelation) and field.rel.to == inline_model and field.object_id_field_name == ct_fk_field and field.content_type_field_name == ct_field:
                        inline_fields.append(field.name)
                _autoregister(admin, inline_model)
            else:
                fk_name = inline.fk_name
                if not fk_name:
                    for field in inline_model._meta.fields:
                        if isinstance(field, (models.ForeignKey, models.OneToOneField)) and issubclass(model, field.rel.to):
                            fk_name = field.name
                _autoregister(admin, inline_model, follow=[fk_name])
                if not inline_model._meta.get_field(fk_name).rel.is_hidden():
                    accessor = inline_model._meta.get_field(fk_name).related.get_accessor_name()
                    inline_fields.append(accessor)
        _autoregister(admin, model, inline_fields)

def registe_models(admin_site=None):
    if admin_site is None:
        admin_site = site

    for model, admin in admin_site._registry.items():
        if getattr(admin, 'reversion_enable', False):
            _registe_model(admin, model)

class ReversionPlugin(BaseAdminPlugin):
    
    # The revision manager instance used to manage revisions.
    revision_manager = default_revision_manager
    
    # The serialization format to use when registering models with reversion.
    reversion_format = "json"
    
    # Whether to ignore duplicate revision data.
    ignore_duplicate_revisions = False

    reversion_enable = False

    def init_request(self, *args, **kwargs):
        return self.reversion_enable

    @property
    def revision_context_manager(self):
        """The revision context manager for this VersionAdmin."""
        return self.revision_manager._revision_context_manager

    def get_revision_instances(self, obj):
        """Returns all the instances to be used in the object's revision."""
        return [obj]
    
    def get_revision_data(self, obj, flag):
        """Returns all the revision data to be used in the object's revision."""
        return dict(
            (o, self.revision_manager.get_adapter(o.__class__).get_version_data(o, flag))
            for o in self.get_revision_instances(obj)
        )

    def save_revision(self, obj, tag, comment):
        self.revision_manager.save_revision(
            self.get_revision_data(obj, tag),
            user = self.user,
            comment = comment,
            ignore_duplicates = self.ignore_duplicate_revisions,
            db = self.revision_context_manager.get_db(),
        )

    def do_post(self, __):
        def _method():
            self.revision_context_manager.set_user(self.user)
            comment = ''
            admin_view = self.admin_view
            if isinstance(admin_view, CreateAdminView):
                comment = _(u"Initial version.")
            elif isinstance(admin_view, UpdateAdminView):
                comment = _(u"Change version.")
            elif isinstance(admin_view, DeleteAdminView):
                comment = _(u"Deleted %(verbose_name)s.") % {"verbose_name": self.opts.verbose_name}
            self.revision_context_manager.set_comment(comment)
            return __()
        return _method

    def post(self, __, request, *args, **kwargs):
        return self.revision_context_manager.create_revision(manage_manually=False)(self.do_post(__))()

    # def save_models(self, __):
    #     self.revision_context_manager.create_revision(manage_manually=True)(__)()

    #     if self.admin_view.org_obj is None:
    #         self.save_revision(self.admin_view.new_obj, VERSION_ADD, _(u"Initial version."))
    #     else:
    #         self.save_revision(self.admin_view.new_obj, VERSION_CHANGE, _(u"Change version."))

    # def save_related(self, __):
    #     self.revision_context_manager.create_revision(manage_manually=True)(__)()

    # def delete_model(self, __):
    #     self.save_revision(self.admin_view.obj, VERSION_DELETE, \
    #         _(u"Deleted %(verbose_name)s.") % {"verbose_name": self.opts.verbose_name})
    #     self.revision_context_manager.create_revision(manage_manually=True)(__)()


    # Block Views
    def block_top_toolbar(self, context, nodes):
        recoverlist_url = self.admin_view.model_admin_urlname('recoverlist')
        nodes.append(mark_safe('<a class="btn btn-small" href="%s"><i class="icon-trash"></i> %s</a>' % (recoverlist_url, _(u"Recover deleted"))))

    def block_object_tools(self, context, nodes):
        obj = getattr(self.admin_view, 'org_obj', getattr(self.admin_view, 'obj', None))
        if obj:
            revisionlist_url = self.admin_view.model_admin_urlname('revisionlist', quote(obj.pk))
            nodes.append(mark_safe('<a href="%s" class="btn btn-small"><i class="icon-time"></i> %s</a>' % (revisionlist_url, _(u'History'))))

class BaseReversionView(ModelAdminView):

    # The revision manager instance used to manage revisions.
    revision_manager = default_revision_manager
    
    # The serialization format to use when registering models with reversion.
    reversion_format = "json"
    
    # Whether to ignore duplicate revision data.
    ignore_duplicate_revisions = False
    
    # If True, then the default ordering of object_history and recover lists will be reversed.
    history_latest_first = False

    reversion_enable = False

    def init_request(self, *args, **kwargs):
        if not self.has_change_permission() and not self.has_add_permission():
            raise PermissionDenied

    def _order_version_queryset(self, queryset):
        """Applies the correct ordering to the given version queryset."""
        if self.history_latest_first:
            return queryset.order_by("-pk")
        return queryset.order_by("pk")

class RecoverListView(BaseReversionView):

    recover_list_template = None
    
    def get_context(self):
        context = super(RecoverListView, self).get_context()
        opts = self.opts
        deleted = self._order_version_queryset(self.revision_manager.get_deleted(self.model))
        context.update({
            "opts": opts,
            "app_label": opts.app_label,
            "module_name": capfirst(opts.verbose_name),
            "title": _("Recover deleted %(name)s") % {"name": force_unicode(opts.verbose_name_plural)},
            "deleted": deleted,
            "changelist_url": self.model_admin_urlname("changelist"),
        })
        return context

    @csrf_protect_m
    def get(self, request, *args, **kwargs):
        context = self.get_context()

        return TemplateResponse(request, self.recover_list_template or self.get_template_list("recover_list.html"),
            context, current_app=self.admin_site.name)
        
class RevisionListView(BaseReversionView):

    object_history_template = None

    def get_context(self):
        context = super(RevisionListView, self).get_context()

        opts = self.opts
        action_list = [
            {
                "revision": version.revision,
                "url": self.model_admin_urlname('revision', quote(version.object_id), version.id),
            }
            for version
            in self._order_version_queryset(self.revision_manager.get_for_object_reference(
                self.model,
                self.obj.pk,
            ).select_related("revision__user"))
        ]
        context.update({
            'title': _('Change history: %s') % force_unicode(self.obj),
            'action_list': action_list,
            'module_name': capfirst(force_unicode(opts.verbose_name_plural)),
            'object': self.obj,
            'app_label': opts.app_label,
            "changelist_url": self.model_admin_urlname("changelist"),
            "update_url": self.model_admin_urlname("change", self.obj.pk),
            'opts': opts,
        })
        return context

    def get(self, request, object_id, *args, **kwargs):
        object_id = unquote(object_id)
        self.obj = self.get_object(object_id)

        if not self.has_change_permission(self.obj):
            raise PermissionDenied

        context = self.get_context()

        return TemplateResponse(request, self.object_history_template or self.get_template_list('object_history.html'),
            context, current_app=self.admin_site.name)

class BaseRevisionView(ModelFormAdminView):

    @filter_hook
    def get_revision(self):
        return self.version.field_dict

    @filter_hook
    def get_form_datas(self):
        datas = {"instance": self.org_obj, "initial": self.get_revision()}
        if self.request_method == 'post':
            datas.update({'data': self.request.POST, 'files': self.request.FILES})
        return datas

    @filter_hook
    def get_context(self):
        context = super(BaseRevisionView, self).get_context()
        context.update({
            'object': self.org_obj
            })
        return context

class RevisionView(BaseRevisionView):

    revision_form_template = None

    def init_request(self, object_id, version_id):
        if not self.has_change_permission():
            raise PermissionDenied

        object_id = unquote(object_id) # Underscores in primary key get quoted to "_5F"
        self.org_obj = self.get_object(object_id)
        self.version = get_object_or_404(Version, pk=version_id, object_id=unicode(self.org_obj.pk))

        self.prepare_form()

    @filter_hook
    def get_context(self):
        context = super(RevisionView, self).get_context()
        context["title"] = _("Revert %s") % force_unicode(self.model._meta.verbose_name)
        return context

    @filter_hook
    def get_response(self):
        context = self.get_context()
        context.update(self.kwargs or {})

        form_template = self.revision_form_template
        return TemplateResponse(self.request, form_template or self.get_template_list('revision_form.html'), \
            context, current_app=self.admin_site.name)

    @filter_hook
    def post_response(self):
        self.message_user(_(u'The %(model)s "%(name)s" was reverted successfully. You may edit it again below.') % \
            {"model": force_unicode(self.opts.verbose_name), "name": unicode(self.new_obj)}, 'success')
        return HttpResponseRedirect(self.model_admin_urlname('change', self.new_obj.pk))

class RecoverView(BaseRevisionView):

    recover_form_template = None

    def init_request(self, version_id):
        if not self.has_change_permission() and not self.has_add_permission():
            raise PermissionDenied

        self.version = get_object_or_404(Version, pk=version_id)
        self.org_obj = self.version.object_version.object

        self.prepare_form()

    @filter_hook
    def get_context(self):
        context = super(RecoverView, self).get_context()
        context["title"] = _("Recover %s") % self.version.object_repr
        return context

    @filter_hook
    def get_response(self):
        context = self.get_context()
        context.update(self.kwargs or {})

        form_template = self.recover_form_template
        return TemplateResponse(self.request, form_template or self.get_template_list('recover_form.html'), \
            context, current_app=self.admin_site.name)

    @filter_hook
    def post_response(self):
        self.message_user(_(u'The %(model)s "%(name)s" was recovered successfully. You may edit it again below.') % \
            {"model": force_unicode(self.opts.verbose_name), "name": unicode(self.new_obj)}, 'success')
        return HttpResponseRedirect(self.model_admin_urlname('change', self.new_obj.pk))

# inline hack plugin
class InlineRevisionPlugin(BaseAdminPlugin):

    def get_related_versions(self, obj, version, formset):
        """Retreives all the related Version objects for the given FormSet."""
        object_id = obj.pk
        # Get the fk name.
        try:
            fk_name = formset.fk.name
        except AttributeError:
            # This is a GenericInlineFormset, or similar.
            fk_name = formset.ct_fk_field.name
        # Look up the revision data.
        revision_versions = version.revision.version_set.all()
        related_versions = dict([(related_version.object_id, related_version)
                                 for related_version in revision_versions
                                 if ContentType.objects.get_for_id(related_version.content_type_id).model_class() == formset.model
                                 and unicode(related_version.field_dict[fk_name]) == unicode(object_id)])
        return related_versions
    
    def _hack_inline_formset_initial(self, revision_view, formset):
        """Hacks the given formset to contain the correct initial data."""
        # Now we hack it to push in the data from the revision!
        initial = []
        related_versions = self.get_related_versions(revision_view.org_obj, revision_view.version, formset)
        formset.related_versions = related_versions
        for related_obj in formset.queryset:
            if unicode(related_obj.pk) in related_versions:
                initial.append(related_versions.pop(unicode(related_obj.pk)).field_dict)
            else:
                initial_data = model_to_dict(related_obj)
                initial_data["DELETE"] = True
                initial.append(initial_data)
        for related_version in related_versions.values():
            initial_row = related_version.field_dict
            pk_name = ContentType.objects.get_for_id(related_version.content_type_id).model_class()._meta.pk.name
            del initial_row[pk_name]
            initial.append(initial_row)
        # Reconstruct the forms with the new revision data.
        formset.initial = initial
        formset.forms = [formset._construct_form(n) for n in xrange(len(initial))]
        # Hack the formset to force a save of everything.
        def get_changed_data(form):
            return [field.name for field in form.fields]
        for form in formset.forms:
            form.has_changed = lambda: True
            form._get_changed_data = partial(get_changed_data, form=form)
        def total_form_count_hack(count):
            return lambda: count
        formset.total_form_count = total_form_count_hack(len(initial))

    def instance_form(self, formset, **kwargs):
        admin_view = self.admin_view.admin_view
        if hasattr(admin_view, 'version') and hasattr(admin_view, 'org_obj'):
            self._hack_inline_formset_initial(admin_view, formset)
        return formset

# action revision
class ActionRevisionPlugin(BaseAdminPlugin):

    revision_manager = default_revision_manager
    reversion_enable = False

    def init_request(self, *args, **kwargs):
        return self.reversion_enable

    @property
    def revision_context_manager(self):
        return self.revision_manager._revision_context_manager

    def do_action_func(self, __):
        def _method():
            self.revision_context_manager.set_user(self.user)
            action_view = self.admin_view
            comment = action_view.description % model_format_dict(self.opts)

            self.revision_context_manager.set_comment(comment)
            return __()
        return _method

    def do_action(self, __, queryset):
        return self.revision_context_manager.create_revision(manage_manually=False)(self.do_action_func(__))()

site.register(Revision)
site.register(Version)

site.register_modelview(r'^recover/$', RecoverListView, name='%s_%s_recoverlist')
site.register_modelview(r'^recover/([^/]+)/$', RecoverView, name='%s_%s_recover')
site.register_modelview(r'^([^/]+)/revision/$', RevisionListView, name='%s_%s_revisionlist')
site.register_modelview(r'^([^/]+)/revision/([^/]+)/$', RevisionView, name='%s_%s_revision')

site.register_plugin(ReversionPlugin, ListAdminView)
site.register_plugin(ReversionPlugin, ModelFormAdminView)
site.register_plugin(ReversionPlugin, DeleteAdminView)

site.register_plugin(InlineRevisionPlugin, InlineModelAdmin)
site.register_plugin(ActionRevisionPlugin, BaseActionView)


