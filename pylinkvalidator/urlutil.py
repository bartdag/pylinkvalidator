# -*- coding: utf-8 -*-
"""
Contains the crawling logic.
"""
from __future__ import unicode_literals, absolute_import

from pylinkvalidator.compat import urlparse


SCHEME_HTTP = "http"
SCHEME_HTTPS = "https"
SUPPORTED_SCHEMES = (SCHEME_HTTP, SCHEME_HTTPS)


NOT_LINK = [
    'data',
    '#',
]


def is_link(url):
    """Return True if the url is not base 64 data or a local ref (#)"""
    for prefix in NOT_LINK:
        if url.startswith(prefix):
            return False
    return True


def get_clean_url_split(url):
    """Returns a clean SplitResult with a scheme and a valid path

    :param url: The url to clean
    :rtype: A urlparse.SplitResult
    """
    if not url:
        raise ValueError('The URL must not be empty')
    split_result = urlparse.urlsplit(url)

    if not split_result.scheme:
        if split_result.netloc:
            url = SCHEME_HTTP + ":" + url
        else:
            url = SCHEME_HTTP + "://" + url
        split_result = urlparse.urlsplit(url)

    return split_result


def get_absolute_url_split(url, base_url_split):
    """Returns a SplitResult containing the new URL.

    :param url: The url (relative or absolute).
    :param base_url_split: THe SplitResult of the base URL.
    :rtype: A SplitResult
    """
    new_url = urlparse.urljoin(base_url_split.geturl(), url)

    return get_clean_url_split(new_url)
