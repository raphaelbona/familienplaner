"""Chefkoch.de source (the only default site search).

robots.txt of chefkoch.de disallows only sorted/filtered search variants (/rs/s0t*, /rs/s0o*,
/rs/s0e*, /rs/s0g*, /rs/s0r*, /rs/s0p*, /rs/s0ip*). We therefore fetch ONLY the plain form
``/rs/s0/<term>/Rezepte.html`` plus individual recipe pages ``/rezepte/<id>/<slug>.html``.
``search_path_re`` and ``recipe_link_re`` enforce this for every request and every redirect target.
"""

import re
from urllib.parse import quote

from app.core.recipe_sources.site_search import SiteSearchSource

_RECIPE_RE = re.compile(r"/rezepte/[0-9]{1,20}/[A-Za-z0-9._~%-]{1,200}\.html")
_SEARCH_RE = re.compile(r"/rs/s0/[^/]{1,200}/Rezepte\.html")


class ChefkochSearchSource(SiteSearchSource):
    domain = "chefkoch.de"
    display_name = "Chefkoch.de"
    recipe_link_re = _RECIPE_RE
    search_path_re = _SEARCH_RE

    def search_url(self, term: str) -> str:
        return f"https://www.chefkoch.de/rs/s0/{quote(term, safe='')}/Rezepte.html"


# domain -> site source implementation; configured domains without an entry are only used for adoption.
SITE_SOURCES: dict[str, type[SiteSearchSource]] = {"chefkoch.de": ChefkochSearchSource}
