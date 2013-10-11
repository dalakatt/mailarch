from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import user_passes_test
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.forms.formsets import formset_factory
from django.http import HttpResponseRedirect, HttpResponse
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from haystack.views import SearchView, FacetedSearchView
from haystack.query import SearchQuerySet
from mlarchive.utils.decorators import check_access, superuser_only
from mlarchive.archive import actions
from mlarchive.archive.query_utils import get_kwargs
from mlarchive.archive.view_funcs import initialize_formsets, get_columns, get_export

from models import *
from forms import *

import re
import os

from django.utils.log import getLogger
logger = getLogger('mlarchive.custom')

# --------------------------------------------------
# Classes
# --------------------------------------------------
class CustomSearchView(FacetedSearchView):
    '''
    A customized SearchView.  Need to add request object to the form init so we can use it
    for authorization
    '''
    def __name__(self):
        return "CustomSearchView"

    def build_form(self, form_kwargs=None):
        return super(self.__class__,self).build_form(form_kwargs={ 'request' : self.request })

    def extra_context(self):
        extra = super(CustomSearchView, self).extra_context()
        match = re.search(r"^email_list=([a-zA-Z0-9\_\-]+)",self.request.META['QUERY_STRING'])
        if match:
            try:
                browse_list = EmailList.objects.get(name=match.group(1))
            except EmailList.DoesNotExist:
                browse_list = None
        else:
            browse_list = None
        extra['browse_list'] = browse_list
        match = re.search(r"so=thread",self.request.META['QUERY_STRING'])
        if match:
            extra['thread_sorted'] = True
        else:
            extra['thread_sorted'] = False

        extra['export_mbox'] = self.request.META['REQUEST_URI'].replace('/archive/search/','/archive/export/mbox/')
        extra['export_maildir'] = self.request.META['REQUEST_URI'].replace('/archive/search/','/archive/export/maildir/')
        if 'as' not in self.request.GET:
            extra['modify_search_url'] = self.request.META['REQUEST_URI'].replace('/archive/search/','/archive/')
        else:
            extra['modify_search_url'] = self.request.META['REQUEST_URI'].replace('/archive/search/','/archive/advsearch/')
        return extra

    # override this for export
    #def create_response(self):

# --------------------------------------------------
# STANDARD VIEW FUNCTIONS
# --------------------------------------------------
#@user_passes_test(lambda u: u.is_superuser)
@superuser_only
def admin(request):
    '''
    Administrator View.  Only accessible by the superuser this view allows
    the administrator to delete spam messages
    '''
    results = None
    if request.method == 'POST':
        if 'action' not in request.POST:
            form = AdminForm(request.POST)
            if form.is_valid():
                kwargs = get_kwargs(form.cleaned_data)
                if kwargs:
                    results = SearchQuerySet().filter(**kwargs)
        else:
            action = request.POST.get('action')
            func = getattr(actions, action)
            selected = request.POST.getlist('_selected_action')
            queryset = Message.objects.filter(pk__in=selected)
            return func(request, queryset)

    else:
        form = AdminForm()

    return render_to_response('archive/admin.html', {
        'results': results,
        'form': form},
        RequestContext(request, {}),
    )

def advsearch(request):
    '''
    The Advanced Search View
    '''
    if request.GET:
        # reverse engineer advanced search form from query string
        form = AdvancedSearchForm(request=request,initial=request.GET)
        query_formset, not_formset = initialize_formsets(request.GET.get('q'))

    else:
        form = AdvancedSearchForm(request=request)
        RulesFormset = formset_factory(RulesForm)
        query_formset = RulesFormset(prefix='query')
        not_formset = RulesFormset(prefix='not')

    return render_to_response('archive/advsearch.html', {
        'form': form,
        'query_formset': query_formset,
        'not_formset': not_formset},
        RequestContext(request, {}),
    )

def browse(request):
    '''
    This view presents a list of Email Lists the user has access to.  There are
    separate sections for private, active and inactive.
    '''
    form = BrowseForm()
    columns = get_columns(request.user)

    return render_to_response('archive/browse.html', {
        'form': form,
        'columns': columns},
        RequestContext(request, {}),
    )

# TODO if we use this, need access decorator
def browse_list(request, list_name):
    '''
    Browse emails by list.  The simple version.
    '''
    list_obj = get_object_or_404(EmailList, name=list_name)

    # default sort order is date descending
    order = request.GET.get('so','-date')
    if not order in ('date','-date','frm','-frm'):
        order = '-date'
    msgs = Message.objects.filter(email_list=list_obj).order_by(order)

    return render_to_response('archive/browse_list.html', {
        'list_obj': list_obj,
        'msgs': msgs},
        RequestContext(request, {}),
    )

def console(request):
    form = None
    return render_to_response('archive/console.html', {
        'form': form},
        RequestContext(request, {}),
    )
    
@check_access
def detail(request, list_name, id, msg):
    '''
    This view displays the requested message.
    NOTE: the "msg" argument is a Message object added by the check_access decorator
    '''
    msg_html = msg.get_body_html()

    return render_to_response('archive/detail.html', {
        'msg_html': msg_html},
        RequestContext(request, {}),
    )

def export(request, type):
    '''
    This view takes a search query string and builds a gzipped tar archive of the messages
    in the query results.  Two formats are supported: maildir and mbox.
    '''
    # force sort order and run query
    data = request.GET.copy()
    data['so'] = 'email_list'
    data['sso'] = 'date'
    form = AdvancedSearchForm(data,load_all=False,request=request)
    queryset = form.search()

    # don't allow export of huge querysets and skip empty querysets
    count = queryset.count()
    if count > 50000:
        # message user
        # return to original query
        raise Exception
    elif count == 0:
        # message user
        # return to original query
        pass

    tardata, filename = get_export(queryset, type)

    response = HttpResponse(tardata.read())
    response['Content-Disposition'] = 'attachment; filename=%s' % filename
    response['Content-Type'] = 'application/x-gzip'
    tardata.close()
    return response

def logout_view(request):
    '''
    Logout the user
    '''
    logout(request)
    return HttpResponseRedirect('/archive/')

def main(request):
    '''
    The main page.  This page contains a simple search form and some links.
    '''
    if request.GET:
        form = SearchForm(request.GET)
    else:
        form = SearchForm()

    if os.path.exists(settings.LOG_FILE):
        try:
            os.chmod(settings.LOG_FILE,0666)
        except OSError:
            pass

    return render_to_response('archive/main.html', {
        'form': form},
        RequestContext(request, {}),
    )

# --------------------------------------------------
# TEST FUNCTIONS
# --------------------------------------------------
def test(request):
    #from django.core.exceptions import PermissionDenied
    #raise PermissionDenied
    tests = [u'Ond\u0159ej Sur\xfd<ondrej.sury@nic.cz>',
            u'P \xe4r Mattsson <per@defero.se>']
    msg = Message.objects.get(id=1)
    body = msg.get_body()
    return render_to_response('archive/test.html', {
        'tests': tests,
        'body': body},
        RequestContext(request, {}),
    )
