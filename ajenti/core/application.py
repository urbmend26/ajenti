import time
import Cookie
import os.path
import hashlib
import traceback

from ajenti.api import *
from ajenti.com import *
from ajenti.plugins import *
from ajenti.utils import *
from ajenti.ui import *
import ajenti.ui.xslt as xslt

from session import *
from auth import *


# Base class for application/plugin infrastructure
class Application (PluginManager, Plugin):

    uri_handlers = Interface(IURLHandler)
    content_providers = Interface(IContentProvider)
    func_providers = Interface(IXSLTFunctionProvider)

    def __init__(self, config=None):
        PluginManager.__init__(self)

        # Init instance variables
        self.template_path = []
        self.template_styles = []
        self.template_scripts = []
        self.config = config
        self.content = {}
        self.log = config.get('log_facility')
        self.platform = config.get('platform')
        includes = []
        functions = {}
        
        for f in self.func_providers:
            functions.update(f.get_funcs())

        # Get path for static content and templates
        for c in self.content_providers:
            (module, path) = c.content_path()
            self.content[module] = path
            styles = ['/dl/'+module+'/'+s for s in c.css_files]
            self.template_styles.extend(styles)
            scripts = ['/dl/'+module+'/'+s for s in c.js_files]
            self.template_scripts.extend(scripts)

            path = c.widget_path()
            for inc in c.widget_files:
                includes.append(os.path.join(path,inc))
            self.template_path += [c.template_path()]

        if xslt.xslt is None:
            xslt.prepare(
                includes,
                functions
            )
            
        self.log.debug('Initialized')


    def start_response(self, status, headers=[]):
        self.status = status
        self.headers = headers

    def fix_length(self, content):
        # TODO: maybe move this method to middleware
        has_content_length = False
        for header, value in self.headers:
            if header.upper() == 'CONTENT-LENGTH':
                has_content_length = True
        if not has_content_length:
            self.headers.append(('Content-Length',str(len(content))))

    def dispatcher(self, environ, start_response):
        self.log.debug('Dispatching %s'%environ['PATH_INFO'])
        self.environ = environ
        self.status = '200 OK'
        self.headers = [('Content-type','text/html')]
        self.session = environ['app.session']

        content = 'Sorry, no content for you'
        for handler in self.uri_handlers:
            if handler.match_url(environ):
                try:
                    self.log.debug('Calling handler for %s'%environ['PATH_INFO'])
                    content = handler.url_handler(self.environ,
                                                  self.start_response)
                except Exception, e:
                    try:
                        content = format_error(self, e)
                    except:
                        status = '418 I\'m a teapot'
                        content = 'Fatal error occured:\n' + traceback.format_exc()
                finally:
                    break

        start_response(self.status, self.headers)
        self.fix_length(content)
        if not isinstance(content, environ['wsgi.file_wrapper']):
            content = [content]
        self.log.debug('Finishing %s'%environ['PATH_INFO'])
        return content

    def plugin_enabled(self, cls):
        return self.platform.lower() in [x.lower() for x in cls.platform] \
           or 'any' in cls.platform

    def plugin_activated(self, plugin):
        plugin.log = self.log
        plugin.config = self.config
        plugin.app = self

    def grab_plugins(self, iface, flt=None):
        plugins = self.plugin_get(iface)
        if flt:
            plugins = filter(flt, plugins)
        return filter(None, [self.instance_get(cls, True) for cls in plugins])

    def get_backend(self, iface, flt=None):
        lst = self.grab_plugins(iface, flt)
        if len(lst) == 0:
            raise BackendRequirementError(iface.__name__) 
        return lst[0]

    def get_config(self, plugin):
        cfg = self.get_backend(IModuleConfig,  
                flt=lambda x: x.plugin==plugin.plugin_id)
        cfg.overlay_config()
        return cfg
                
    def get_template(self, filename=None, search_path=[]):
        return BasicTemplate(
                filename=filename,
                search_path=self.template_path + search_path,
                styles=self.template_styles,
                scripts=self.template_scripts
               )

class AppDispatcher(object):
    def __init__(self, config=None):
        self.config = config
        self.log = config.get('log_facility')
        # TODO: add config parameter for session timeout
        self.sessions = SessionStore()
        # Ugly hack :) for permanent middleware
        self.dispatcher = AuthManager(self.config, self.dispatcher)

    def dispatcher(self, environ, start_response):
        self.log.debug('Dispatching %s'%environ['PATH_INFO'])
        # Use unique instances for each request,
        # so no plugin data will be interused between different clients
        app = Application(self.config).dispatcher
        app = SessionManager(self.sessions, app)

        return app(environ, start_response)