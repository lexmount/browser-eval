"""Public report URL shared by the article export and standalone packager."""
import os

HUB = (os.environ.get('WEBFETCH_REPORT_PUBLIC_URL') or
       'https://artifact-hub.dev.lexmount.net/api/preview/bWkRP157EtF6').rstrip('/')
