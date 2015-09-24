# -*- coding: utf-8 -*-
"""
Contains the crawling models. We use namedtuple for most models (easier to
pickle, lower footprint, indicates that it is immutable) and we use classes for
objects with mutable states and helper methods.

Classes with crawling logic are declared in the crawler module.
"""
from __future__ import unicode_literals, absolute_import

from collections import namedtuple, Mapping, defaultdict
from optparse import OptionParser, OptionGroup
import re

from pylinkvalidator.included.bs4 import BeautifulSoup
from pylinkvalidator.compat import get_safe_str
from pylinkvalidator.urlutil import get_clean_url_split, get_absolute_url_split

PREFIX_ALL = "*"

REGEX_CONTENT = "regex:"


def namedtuple_with_defaults(typename, field_names, default_values=[]):
    """Creates a namedtuple with default values so they don't have to be
    provided for each argument.
    """
    T = namedtuple(typename, field_names)

    # Set None everywhere
    T.__new__.__defaults__ = (None,) * len(T._fields)

    # Set provided default values
    if isinstance(default_values, Mapping):
        prototype = T(**default_values)
    else:
        prototype = T(*default_values)
    T.__new__.__defaults__ = tuple(prototype)

    # Return new type
    return T


DEFAULT_TYPES = ['a', 'img', 'script', 'link']


TYPE_ATTRIBUTES = {
    'a': 'href',
    'img': 'src',
    'script': 'src',
    'link': 'href',
}


DEFAULT_TIMEOUT = 10


MODE_THREAD = "thread"
MODE_PROCESS = "process"
MODE_GREEN = "green"


DEFAULT_WORKERS = {
    MODE_THREAD: 1,
    MODE_PROCESS: 1,
    MODE_GREEN: 1000,
}


PARSER_STDLIB = "html.parser"
PARSER_LXML = "lxml"
PARSER_HTML5 = "html5lib"

# TODO Add support for gumbo. Will require some refactoring of the parsing
# logic.
# PARSER_GUMBO = "gumbo"


FORMAT_PLAIN = "plain"
FORMAT_HTML = "html"
FORMAT_JSON = "json"


WHEN_ALWAYS = "always"
WHEN_ON_ERROR = "error"


REPORT_TYPE_ERRORS = "errors"
REPORT_TYPE_SUMMARY = "summary"
REPORT_TYPE_ALL = "all"


VERBOSE_QUIET = "0"
VERBOSE_NORMAL = "1"
VERBOSE_INFO = "2"


HTML_MIME_TYPE = "text/html"


PAGE_QUEUED = '__PAGE_QUEUED__'
PAGE_CRAWLED = '__PAGE_CRAWLED__'

# Note: we use namedtuple to exchange data with workers because they are
# immutable and easy to pickle (as opposed to a class).

WorkerInit = namedtuple_with_defaults(
    "WorkerInit",
    ["worker_config", "input_queue", "output_queue", "logger"])


WorkerConfig = namedtuple_with_defaults(
    "WorkerConfig",
    ["username", "password", "types", "timeout", "parser", "strict_mode",
     "prefer_server_encoding", "extra_headers"])


WorkerInput = namedtuple_with_defaults(
    "WorkerInput",
    ["url_split", "should_crawl", "depth", "site_origin", "content_check"])


Response = namedtuple_with_defaults(
    "Response", ["content", "status", "exception", "original_url",
                 "final_url", "is_redirect", "is_timeout", "response_time"])


ExceptionStr = namedtuple_with_defaults(
    "ExceptionStr", ["type_name", "message"])


Link = namedtuple_with_defaults(
    "Link",
    ["type", "url_split", "original_url_split", "source_str"])


PageCrawl = namedtuple_with_defaults(
    "PageCrawl", ["original_url_split", "final_url_split",
                  "status", "is_timeout", "is_redirect", "links",
                  "exception", "is_html", "depth", "response_time",
                  "process_time", "site_origin", "missing_content",
                  "erroneous_content"])


PageStatus = namedtuple_with_defaults(
    "PageStatus", ["status", "sources"])


PageSource = namedtuple_with_defaults(
    "PageSource", ["origin", "origin_str"])


ContentCheck = namedtuple_with_defaults(
    "ContentCheck",
    ["html_presence", "html_absence", "text_presence", "text_absence",
     "has_something_to_check"])

HTMLCheck = namedtuple_with_defaults(
    "HTMLCheck", ["tag", "attrs", "content"])


class UTF8Class(object):
    """Handles unicode string from __unicode__() in: __str__() and __repr__()
    """
    def __str__(self):
        return get_safe_str(self.__unicode__())

    def __repr__(self):
        return get_safe_str(self.__unicode__())


class LazyLogParam(object):
    """Lazy Log Parameter that is only evaluated if the logging statement
       is printed"""

    def __init__(self, func):
        self.func = func

    def __str__(self):
        return str(self.func())


class Config(UTF8Class):
    """Contains all the configuration options."""

    def __init__(self):
        # Design note: we only use attributes when options need to be
        # transformed. Otherwise, we use options.
        self.parser = self._build_parser()
        self.options = None
        self.start_urls = []
        self.start_url_splits = []
        self.worker_config = None

        self.accepted_hosts = []
        """Set of accepted hosts. Dictionary of accepted hosts if in multi
        mode: key: start url host, value: set of accepted hosts."""

        self.ignored_prefixes = []
        self.worker_size = 0
        self.content_check = None

    def should_crawl(self, url_split, depth):
        """Returns True if url split is local AND depth is acceptable"""
        return (self.options.depth < 0 or depth < self.options.depth) and\
            self.is_local(url_split)

    def is_local(self, url_split, site_origin=None):
        """Returns true if url split is in the accepted hosts. site_origin must
        be provided if multi sites mode is enabled."""

        if self.options.multi and site_origin:
            accepted_hosts = self.accepted_hosts[site_origin]
        else:
            accepted_hosts = self.accepted_hosts

        return url_split.netloc in accepted_hosts

    def should_download(self, url_split):
        """Returns True if the url does not start with an ignored prefix and if
        it is local or outside links are allowed."""
        local = self.is_local(url_split)

        if not self.options.test_outside and not local:
            return False

        url = url_split.geturl()

        for ignored_prefix in self.ignored_prefixes:
            if url.startswith(ignored_prefix):
                return False

        return True

    def parse_cli_config(self):
        """Builds the options and args based on the command line options."""
        (self.options, self.start_urls) = self.parser.parse_args()
        self._parse_config()

    def parse_api_config(self, start_urls, options_dict=None):
        """Builds the options and args based on passed parameters."""
        # TODO Add options
        options = self._get_options(options_dict)
        (self.options, self.start_urls) = self.parser.parse_args(
            options + start_urls)
        self._parse_config()

    def _get_options(self, options_dict):
        if not options_dict:
            options_dict = {}
        options = []
        for key, value in options_dict.items():
            if isinstance(value, bool) and value:
                options.append("--{0}".format(key))
            else:
                options.append("--{0}={1}".format(key, value))
        return options

    def _parse_config(self):
        if self.options.url_file_path:
            self.start_urls = self._read_start_urls(self.options.url_file_path)
        self._process_start_urls()

        self.worker_config = self._build_worker_config(self.options)
        self.accepted_hosts = self._build_accepted_hosts(
            self.options, self.start_urls)

        if self.options.ignored_prefixes:
            self.ignored_prefixes = self.options.ignored_prefixes.split(',')

        if self.options.workers:
            self.worker_size = self.options.workers
        else:
            self.worker_size = DEFAULT_WORKERS[self.options.mode]

        if self.options.run_once:
            self.options.depth = 0

        self.content_check = self._compute_content_check(self.options)

        self._add_content_check_urls(self.start_url_splits, self.content_check)

    def _read_start_urls(self, url_file_path):
        urls = []
        with open(url_file_path, "r") as url_file:
            urls = [url for url in url_file.read().split() if url]
        return urls

    def _process_start_urls(self):
        for start_url in self.start_urls:
            self.start_url_splits.append(get_clean_url_split(start_url))

    def _build_worker_config(self, options):
        types = options.types.split(',')
        for element_type in types:
            if element_type not in DEFAULT_TYPES:
                raise ValueError("This type is not supported: {0}"
                                 .format(element_type))

        headers = {}
        if options.headers:
            for item in options.headers:
                split = item.split(":")
                if len(split) == 2:
                    headers[split[0]] = split[1]

        return WorkerConfig(
            options.username, options.password, types, options.timeout,
            options.parser, options.strict_mode,
            options.prefer_server_encoding, headers)

    def _build_accepted_hosts(self, options, start_urls):
        if options.multi:
            return self._build_multi_hosts(options, start_urls)
        else:
            return self._build_single_hosts(options, start_urls)

    def _build_multi_hosts(self, options, start_urls):
        hosts = {}

        extra_hosts = set()
        if options.accepted_hosts:
            for url in options.accepted_hosts.split(','):
                split_result = get_clean_url_split(url)
                extra_hosts.add(split_result.netloc)

        for start_url in start_urls:
            split_result = get_clean_url_split(start_url)
            host = split_result.netloc
            hosts[host] = extra_hosts.union(host)

        return hosts

    def _build_single_hosts(self, options, start_urls):
        hosts = set()
        urls = []

        if options.accepted_hosts:
            urls = options.accepted_hosts.split(',')
        urls = urls + start_urls

        for url in urls:
            split_result = get_clean_url_split(url)
            hosts.add(split_result.netloc)

        return hosts

    def _compute_content_check(self, options):
        html_presence = defaultdict(list)
        html_absence = defaultdict(list)
        raw_presence = defaultdict(list)
        raw_absence = defaultdict(list)
        self._compute_single_content_check(
            options.content_presence, html_presence,
            raw_presence, PREFIX_ALL)
        self._compute_single_content_check(
            options.content_absence, html_absence,
            raw_absence, PREFIX_ALL)
        self._compute_single_content_check(
            options.content_presence_once, html_presence,
            raw_presence)
        self._compute_single_content_check(
            options.content_absence_once, html_absence,
            raw_absence)

        has_something_to_check = bool(
            html_presence or html_absence or raw_presence or raw_absence)

        return ContentCheck(
            html_presence, html_absence, raw_presence, raw_absence,
            has_something_to_check)

    def _add_content_check_urls(self, start_urls, content_check):
        self._add_urls_from_single_content_check(
            start_urls, content_check.html_presence)
        self._add_urls_from_single_content_check(
            start_urls, content_check.html_absence)
        self._add_urls_from_single_content_check(
            start_urls, content_check.text_presence)
        self._add_urls_from_single_content_check(
            start_urls, content_check.text_absence)

    def _add_urls_from_single_content_check(
            self, start_urls, single_content_check):
        for key in single_content_check.keys():
            if key == PREFIX_ALL:
                continue
            if key.netloc and key not in start_urls:
                start_urls.append(key)
            else:
                for url_split in start_urls:
                    new_url = get_absolute_url_split(
                        key.geturl(), url_split)
                    if new_url not in start_urls:
                        start_urls.append(new_url)

    def _compute_single_content_check(
            self, content_list, html_dict, raw_dict, prefix=None):
        if not content_list:
            # Catch None
            return

        for content in content_list:
            temp_prefix, content = self._get_prefix_content(content, prefix)
            content = content.strip()
            if content.startswith("<"):
                # html.parser because we do not want to automatically create
                # surrounding tags
                soup = BeautifulSoup(content, "html.parser")
                children = list(soup.children)
                if children:
                    child = children[0]
                    string = child.string
                    if child.string and child.string.startswith(REGEX_CONTENT):
                        string = re.compile(child.string[len(REGEX_CONTENT):],
                                            re.MULTILINE)
                    html_check = HTMLCheck(
                        child.name, child.attrs, string)
                    html_dict[temp_prefix].append(html_check)
            else:
                if content and content.startswith(REGEX_CONTENT):
                    content = re.compile(content[len(REGEX_CONTENT):],
                                         re.MULTILINE)
                raw_dict[temp_prefix].append(content)

    def _get_prefix_content(self, content, prefix=None):
        if not prefix:
            index = content.find(",")
            prefix = get_clean_url_split(content[:index])
            content = content[index+1:]

        return (prefix, content)

    def _build_parser(self):
        # avoid circular references
        import pylinkvalidator
        version = pylinkvalidator.__version__

        parser = OptionParser(
            usage="%prog [options] URL ...",
            version="%prog {0}".format(version))

        parser.add_option(
            "-V", "--verbose", dest="verbose", action="store",
            default=VERBOSE_QUIET, choices=[VERBOSE_QUIET, VERBOSE_NORMAL,
                                            VERBOSE_INFO])

        crawler_group = OptionGroup(
            parser, "Crawler Options",
            "These options modify the way the crawler traverses the site.")
        crawler_group.add_option(
            "-O", "--test-outside", dest="test_outside",
            action="store_true", default=False,
            help="fetch resources from other domains without crawling them")
        crawler_group.add_option(
            "-H", "--accepted-hosts",
            dest="accepted_hosts",  action="store", default=None,
            help="comma-separated list of additional hosts to crawl (e.g., "
            "example.com,subdomain.another.com)")
        crawler_group.add_option(
            "-i", "--ignore", dest="ignored_prefixes",
            action="store", default=None,
            help="comma-separated list of host/path prefixes to ignore "
            "(e.g., www.example.com/ignore_this_and_after/)")
        crawler_group.add_option(
            "-u", "--username", dest="username",
            action="store", default=None,
            help="username to use with basic HTTP authentication")
        crawler_group.add_option(
            "-p", "--password", dest="password",
            action="store", default=None,
            help="password to use with basic HTTP authentication")
        crawler_group.add_option(
            "-M", "--multi", dest="multi",
            action="store_true", default=False,
            help="each argument is considered to be a different site")
        crawler_group.add_option(
            "-D", "--header",
            dest="headers",  action="append", metavar="HEADER",
            help="custom header of the form Header: Value "
            "(repeat for multiple headers)")
        crawler_group.add_option(
            "--url-file-path", dest="url_file_path",
            action="store", default=None,
            help="get starting URLs from a line-separated file")
        # crawler_group.add_option("-U", "--unique", dest="unique",
        #         action="store_true", default=False)
        crawler_group.add_option(
            "-t", "--types", dest="types", action="store",
            default=",".join(DEFAULT_TYPES),
            help="Comma-separated values of tags to look for when crawling"
            "a site. Default (and supported types): a,img,link,script")
        crawler_group.add_option(
            "-T", "--timeout", dest="timeout",
            type="int", action="store", default=DEFAULT_TIMEOUT,
            help="Seconds to wait before considering that a page timed out")
        crawler_group.add_option(
            "-C", "--strict", dest="strict_mode",
            action="store_true", default=False,
            help="Does not strip href and src attributes from whitespaces")
        crawler_group.add_option(
            "-P", "--progress", dest="progress",
            action="store_true", default=False,
            help="Prints crawler progress in the console")
        crawler_group.add_option(
            "-N", "--run-once", dest="run_once",
            action="store_true", default=False,
            help="Only crawl the first page (eq. to depth=0).")
        crawler_group.add_option(
            "-d", "--depth", dest="depth",
            type="int", action="store", default=-1,
            help="Maximum crawl depth")
        crawler_group.add_option(
            "-e", "--prefer-server-encoding", dest="prefer_server_encoding",
            action="store_true", default=False,
            help="Prefer server encoding if specified. Else detect encoding")
        crawler_group.add_option(
            "--check-presence", dest="content_presence",
            action="append",
            help="Check presence of raw or HTML content on all pages. e.g., "
            "<tag attr1=\"val\">regex:content</tag>. "
            "Content can be either regex:pattern or plain content")
        crawler_group.add_option(
            "--check-absence", dest="content_absence",
            action="append",
            help="Check absence of raw or HTML content on all pages. e.g., "
            "<tag attr1=\"val\">regex:content</tag>. "
            "Content can be either regex:pattern or plain content")
        crawler_group.add_option(
            "--check-presence-once", dest="content_presence_once",
            action="append",
            help="Check presence of raw or HTML content for one page: "
            "path,content, e.g.,: "
            "/path,<tag attr1=\"val\">regex:content</tag>. "
            "Content can be either regex:pattern or plain content. "
            "Path can be either relative or absolute with domain.")
        crawler_group.add_option(
            "--check-absence-once", dest="content_absence_once",
            action="append",
            help="Check absence of raw or HTML content for one page: "
            "path,content, e.g.,"
            "path,<tag attr1=\"val\">regex:content</tag>. "
            "Content can be either regex:pattern or plain content. "
            "Path can be either relative or absolute with domain.")

        # TODO Add follow redirect option.

        parser.add_option_group(crawler_group)

        perf_group = OptionGroup(
            parser, "Performance Options",
            "These options can impact the performance of the crawler.")

        perf_group.add_option(
            "-w", "--workers", dest="workers", action="store",
            default=None, type="int",
            help="Number of workers to spawn")
        perf_group.add_option(
            "-m", "--mode", dest="mode", action="store",
            help="Types of workers: thread (default), process, or green",
            default=MODE_THREAD, choices=[MODE_THREAD, MODE_PROCESS,
                                          MODE_GREEN])
        perf_group.add_option(
            "-R", "--parser", dest="parser", action="store",
            help="Types of HTML parse: html.parser (default), lxml, html5lib",
            default=PARSER_STDLIB, choices=[PARSER_STDLIB, PARSER_LXML,
                                            PARSER_HTML5])

        parser.add_option_group(perf_group)

        output_group = OptionGroup(
            parser, "Output Options",
            "These options change the output of the crawler.")

        output_group.add_option(
            "-f", "--format", dest="format", action="store",
            default=FORMAT_PLAIN, choices=[FORMAT_PLAIN],
            help="Format of the report: plain")
        output_group.add_option(
            "-o", "--output", dest="output", action="store",
            default=None,
            help="Path of the file where the report will be printed.")
        output_group.add_option(
            "-W", "--when", dest="when", action="store",
            default=WHEN_ALWAYS, choices=[WHEN_ALWAYS, WHEN_ON_ERROR],
            help="When to print the report. error (only if a "
            "crawling error occurs) or always (default)")
        output_group.add_option(
            "-E", "--report-type", dest="report_type",
            help="Type of report to print: errors (default, summary and "
            "erroneous links), summary, all (summary and all links)",
            action="store", default=REPORT_TYPE_ERRORS,
            choices=[REPORT_TYPE_ERRORS, REPORT_TYPE_SUMMARY, REPORT_TYPE_ALL])
        output_group.add_option(
            "-c", "--console", dest="console",
            action="store_true", default=False,
            help="Prints report to the console in addition to other output"
            " options such as file or email.")
        crawler_group.add_option(
            "-S", "--show-source", dest="show_source",
            action="store_true", default=False,
            help="Show source of links (html) in the report.")

        parser.add_option_group(output_group)

        email_group = OptionGroup(
            parser, "Email Options",
            "These options allows the crawler to send a report by email.")

        email_group.add_option(
            "-a", "--address", dest="address", action="store",
            default=None,
            help="Comma-separated list of email addresses used to send a "
            "report")
        email_group.add_option(
            "--from", dest="from_address", action="store",
            default=None,
            help="Email address to use in the from field of the email "
            "(optional)")
        email_group.add_option(
            "-s", "--smtp", dest="smtp", action="store",
            default=None,
            help="Host of the smtp server")
        email_group.add_option(
            "--port", dest="port", action="store",
            default=25, type="int",
            help="Port of the smtp server (optional)")
        email_group.add_option(
            "--tls", dest="tls", action="store_true",
            default=False,
            help="Use TLS with the email server.")
        email_group.add_option(
            "--subject", dest="subject", action="store",
            default=None,
            help="Subject of the email (optional)")
        email_group.add_option(
            "--smtp-username", dest="smtp_username",
            action="store", default=None,
            help="Username to use with the smtp server (optional)")
        email_group.add_option(
            "--smtp-password", dest="smtp_password",
            action="store", default=None,
            help="Password to use with the smtp server (optional)")

        parser.add_option_group(email_group)

        return parser

    def __unicode__(self):
        return "Configuration - Start URLs: {0} - Options: {1}".format(
            self.start_urls, self.options)


class SitePage(UTF8Class):
    """Contains the crawling result for a page.

    This is a class because we need to keep track of the various sources
    linking to this page and it must be modified as the crawl progresses.
    """

    def __init__(self, url_split, status=200, is_timeout=False, exception=None,
                 is_html=True, is_local=True, response_time=None,
                 process_time=None, site_origin=None, missing_content=None,
                 erroneous_content=None):
        self.url_split = url_split

        self.original_source = None
        self.sources = []

        self.type = type
        self.status = status
        self.is_timeout = is_timeout
        self.exception = exception
        self.is_html = is_html
        self.is_local = is_local
        self.is_ok = status and status < 400 and not missing_content and\
            not erroneous_content
        self.response_time = response_time
        self.process_time = process_time
        self.site_origin = site_origin

        if missing_content:
            self.missing_content = missing_content
        else:
            self.missing_content = []

        if erroneous_content:
            self.erroneous_content = erroneous_content
        else:
            self.erroneous_content = []

    def add_sources(self, page_sources):
        self.sources.extend(page_sources)

    def get_status_message(self):
        if self.status:
            if self.status < 400:
                return self._compute_ok_status(self.status)
            elif self.status == 404:
                return "not found (404)"
            else:
                return "error (status={0})".format(self.status)
        elif self.is_timeout:
            return "error (timeout)"
        elif self.exception:
            return "error ({0}): {1}".format(
                self.exception.type_name, self.exception.message)
        else:
            return "error"

    def _compute_ok_status(self, status_code):
        if self.missing_content and not self.erroneous_content:
            return "error ({0}) missing content".format(status_code)
        elif self.erroneous_content and not self.missing_content:
            return "error ({0}) erroneous content".format(status_code)
        elif self.erroneous_content and self.missing_content:
            return "error ({0}) missing and erroneous content".format(
                status_code)
        else:
            return "ok ({0})".format(self.status)

    def get_content_messages(self):
        """Gets missing and erroneous content
        """
        messages = [
            "missing content: {0}".format(content) for content in
            self.missing_content] + [
            "erroneous content: {0}".format(content) for content in
            self.erroneous_content]

        return messages

    def __unicode__(self):
        return "Resource {0} - {1}".format(
            self.url_split.geturl(), self.status)
