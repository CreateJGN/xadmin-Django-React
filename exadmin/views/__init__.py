from exadmin.sites import site

from base import BaseAdminPlugin, BaseAdminView, CommAdminView, ModelAdminView

from list import ListAdminView
from edit import CreateAdminView, UpdateAdminView, ModelFormAdminView
from delete import DeleteAdminView
from detail import DetailAdminView
from dataapi import get_urls as dataapi_get_urls, api_manager
from dashboard import Dashboard, BaseWidget, widget_manager
from website import IndexView, LoginView, LogoutView, UserSettingView

# admin site-wide views
site.register_view(r'^$', IndexView, name='index')
site.register_view(r'^login/$', LoginView, name='login')
site.register_view(r'^logout/$', LogoutView, name='logout')

site.register_view(r'^settings/user$', UserSettingView, name='user_settings')

site.register_view(r'^api/', dataapi_get_urls, name='data_api')

site.register_modelview(r'^$', ListAdminView, name='%s_%s_changelist')
site.register_modelview(r'^add/$', CreateAdminView, name='%s_%s_add')
site.register_modelview(r'^(.+)/delete/$', DeleteAdminView, name='%s_%s_delete')
site.register_modelview(r'^(.+)/update/$', UpdateAdminView, name='%s_%s_change')
site.register_modelview(r'^(.+)/detail/$', DetailAdminView, name='%s_%s_detail')
