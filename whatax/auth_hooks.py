"""Alliance Auth hook registration: gated menu item plus URL mount."""

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.utils.translation import gettext_lazy as _

from whatax import urls


class WhataxMenuItem(MenuItemHook):
    def __init__(self):
        super().__init__(
            text=_("Whale Tax"),
            classes="fas fa-coins fa-fw",
            url_name="whatax:index",
            navactive=["whatax:"],
        )
        # Custom template renders an inline whale SVG (no whale glyph in FA Free).
        self.template = "whatax/menuitem.html"

    def render(self, request):
        # Dashboard for basic_access; Structures for the view_structures read role.
        if request.user.has_perm("whatax.basic_access"):
            self.url_name = "whatax:index"
            return super().render(request)
        if request.user.has_perm("whatax.view_structures"):
            self.url_name = "whatax:structures"
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return WhataxMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "whatax", r"^whatax/")
