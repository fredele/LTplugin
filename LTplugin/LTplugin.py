# -*- coding: utf-8 -*-

from gi.repository import GObject, Gtk, Gedit, PeasGtk, Gio, GLib, Pango, Gdk
import urllib.request
import urllib.parse
import json
import threading
import configparser
import os
import gettext
import locale

APPNAME = "LTplugin"
LOCALEDIR = os.path.join(os.path.dirname(__file__), "locale")
locale.setlocale(locale.LC_ALL, '')
gettext.bindtextdomain(APPNAME, LOCALEDIR)
gettext.textdomain(APPNAME)
_ = gettext.gettext

class LTCheckAppActivatable(GObject.Object, Gedit.AppActivatable):
    app = GObject.property(type=Gedit.App)
    __gtype_name__ = "LTCheckAppActivatable"

    def do_activate(self):
        self.menu_ext = self.extend_menu("tools-section")
        item = Gio.MenuItem.new(_("LT Correction"), 'win.toggle_check')
        self.menu_ext.append_menu_item(item)

    def do_deactivate(self):
        self.menu_ext = None

class LTCheckWindowActivatable(GObject.Object, Gedit.ViewActivatable, PeasGtk.Configurable):
    view = GObject.Property(type=Gedit.View)
    __gtype_name__ = "LTCheckWindowActivatable"
    
    
    def get_server_url(self):
        """Return the LanguageTool server URL from the configuration."""
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "config.ini")
        config.read(config_path)
        return config.get("settings", "server_url", fallback="http://localhost:8081")
    
    def get_language(self):
        """Return the configured correction language, or 'auto' by default."""
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "config.ini")
        config.read(config_path)
        return config.get("settings", "language", fallback="auto")

    def set_language(self, lang):
        """Save the correction language to the configuration."""
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "config.ini")
        config.read(config_path)
        if not config.has_section("settings"):
            config.add_section("settings")
        config.set("settings", "language", lang)
        with open(config_path, "w") as f:
            config.write(f)
        self.language = lang
    
    def get_show_tooltip(self):
        """Return True if tooltips are enabled, False otherwise."""
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "config.ini")
        config.read(config_path)
        return config.getboolean("settings", "show_tooltip", fallback=True)

    def set_show_tooltip(self, value):
        """Enable or disable tooltips and save the preference."""
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "config.ini")
        config.read(config_path)
        if not config.has_section("settings"):
            config.add_section("settings")
        config.set("settings", "show_tooltip", "true" if value else "false")
        with open(config_path, "w") as f:
            config.write(f)
        self.show_tooltip = value

    def do_activate(self):
        """Initialize the plugin for the current view."""
        self.buffer = self.view.get_buffer()
        self.errors = []
        self.enabled = True
        self.check_delay = 5000  # 5 seconds
        self.server_url = self.get_server_url()
        self.language = self.get_language()
        self.show_tooltip = self.get_show_tooltip()
        self.view.connect("query-tooltip", self.on_query_tooltip)

        self.ensure_tag()
        self.setup_action()
        self.check_timer_id = None
        self.check_version = 0
        self.buffer.connect("changed", self.on_buffer_changed)

    def on_buffer_changed(self, *args):
        """Trigger a delayed grammar check after text modification."""
        if self.enabled:
            self.check_version += 1  # Increment on each change
            if self.check_timer_id:
                GLib.source_remove(self.check_timer_id)
            self.check_timer_id = GLib.timeout_add(self.check_delay, self.delayed_check, self.check_version)

    def delayed_check(self, version):
        """Call the grammar check after the delay."""
        self.check_timer_id = None
        self.check_text(version)
        return False  # Do not repeat the timer

    def do_deactivate(self):
        """Clean up the plugin when the view is deactivated."""
        self.enabled = False
        if self.check_timer_id:
            GLib.source_remove(self.check_timer_id)
            self.check_timer_id = None
        # Remove highlights
        tag = self.buffer.get_tag_table().lookup("highlight")
        if tag:
            self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())

    def setup_action(self):
        """Create the action to enable/disable grammar checking."""
        action = Gio.SimpleAction.new_stateful(
            "toggle_check",
            None,
            GLib.Variant.new_boolean(self.enabled)
        )
        action.connect("activate", self.on_toggle_LT)
        self.view.get_toplevel().add_action(action)

    def on_toggle_tooltip(self, button):
        """Callback for the tooltip checkbox in preferences."""
        value = button.get_active()
        self.set_show_tooltip(value)
        if self.view is not None:
            self.view.set_has_tooltip(value)

    def on_toggle_LT(self, action, param):
        """Enable or disable grammar checking via the menu."""
        self.enabled = not self.enabled
        action.set_state(GLib.Variant.new_boolean(self.enabled))
        if not self.enabled:
            # Remove highlights only if the checkbox is unchecked
            tag = self.buffer.get_tag_table().lookup("highlight")
            if tag:
                self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
        else:
            # Immediately trigger a check
            if self.check_timer_id:
                GLib.source_remove(self.check_timer_id)
                self.check_timer_id = None
            self.check_text()

    def ensure_tag(self):
        """Create highlight tags for each error category if necessary."""
        tag_table = self.buffer.get_tag_table()
        if not tag_table.lookup("highlight_grammar"):
            self.buffer.create_tag("highlight_grammar", underline=Pango.Underline.ERROR, underline_rgba=Gdk.RGBA(1, 0, 0, 1))  # Red
        if not tag_table.lookup("highlight_typos"):
            self.buffer.create_tag("highlight_typos", underline=Pango.Underline.ERROR, underline_rgba=Gdk.RGBA(1, 0.5, 0, 1))  # Orange
        if not tag_table.lookup("highlight_blue"):
            self.buffer.create_tag("highlight_blue", underline=Pango.Underline.ERROR, underline_rgba=Gdk.RGBA(0, 0.3, 1, 1))  # Blue

    def check_text(self, version=None):
        """Send the text to LanguageTool and apply error highlights."""
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        current_version = version if version is not None else self.check_version

        def worker():
            try:
                query = urllib.parse.urlencode({"language": self.language, "text": text})
                with urllib.request.urlopen(f"{self.server_url}?{query}") as response:
                    result = json.loads(response.read().decode())
                    if "matches" not in result:
                        self.show_status_message("LanguageTool: No errors found.")
                        return
                def apply_results():
                    if current_version != self.check_version:
                        return False
                    self.errors.clear()
                    tag_table = self.buffer.get_tag_table()
                    # Remove all highlight tags
                    for tag_name in ["highlight_grammar", "highlight_typos", "highlight_blue"]:
                        tag = tag_table.lookup(tag_name)
                        if tag:
                            self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
                    for match in result.get("matches", []):
                        offset = match["offset"]
                        length = match["length"]
                        message = match["message"]
                        replacements = match.get("replacements", [])
                        rule = match.get("rule", {})
                        category = rule.get("category", {}).get("id", "")
                        # Choose tag by category
                        if category == "GRAMMAR":
                            tag = tag_table.lookup("highlight_grammar")
                        elif category == "TYPOS":
                            tag = tag_table.lookup("highlight_typos")
                        elif category in ("STYLE", "PUNCTUATION", "CASING"):
                            tag = tag_table.lookup("highlight_blue")
                        else:
                            tag = tag_table.lookup("highlight_grammar")  # Default: red
                        start_iter = self.buffer.get_iter_at_offset(offset)
                        end_iter = self.buffer.get_iter_at_offset(offset + length)
                        self.buffer.apply_tag(tag, start_iter, end_iter)
                        self.errors.append((offset, offset + length, message, replacements, rule))
                    return False

                GLib.idle_add(apply_results)

            except Exception as e:
                self.show_status_message("LanguageTool error: " + str(e))

        threading.Thread(target=worker, daemon=True).start()

    def on_query_tooltip(self, _, x, y, __, tooltip):
        """Show a tooltip on errors when hovering, if enabled."""
        self.show_tooltip = self.get_show_tooltip()
        if not self.show_tooltip:
            return False
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, x, y)
        success, iter_at_pos = self.view.get_iter_at_location(bx, by)
        if not success:
            return False

        word_start = iter_at_pos.copy()
        word_start.backward_word_start()
        offset = word_start.get_offset()

        for start, end, message, replacements, rule in self.errors:
            if start <= offset < end:
                tooltip.set_text(
                    message
                    + "\nRule: "
                    + rule.get("id", "")
                    + " - "
                    + rule.get("description", "")
                    + "\nSuggestions: "
                    + ", ".join(r["value"] for r in replacements)
                )
                return True

        return False

    def do_create_configure_widget(self):
        """Create the configuration widget for the plugin preferences."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        entry = Gtk.Entry()
        self.server_url = self.get_server_url()
        entry.set_text(self.server_url)
        entry.set_placeholder_text("LanguageTool Server")

        # Language selection
        lang_combo = Gtk.ComboBoxText()
        lang_combo.append("auto", _("Automatic detection"))
        lang_combo.append("fr", _("French"))
        lang_combo.append("en-US", _("English (US)"))
        lang_combo.append("en-GB", _("English (UK)"))
        lang_combo.append("de", _("German"))
        lang_combo.append("es", _("Spanish"))
        # Add more languages if needed

        lang_combo.set_active_id(self.get_language())
        lang_combo.set_tooltip_text("Choose the correction language")

        def on_lang_changed(combo):
            """Callback for language change in preferences."""
            lang = combo.get_active_id()
            self.set_language(lang)

        lang_combo.connect("changed", on_lang_changed)

        def on_changed(widget):
            """Callback for server URL change in preferences."""
            self.server_url = widget.get_text()
            config = configparser.ConfigParser()
            config_path = os.path.join(os.path.dirname(__file__), "config.ini")
            config.read(config_path)
            if not config.has_section("settings"):
                config.add_section("settings")
            config.set("settings", "server_url", entry.get_text())
            with open(config_path, "w") as f:
                config.write(f)

        entry.connect("changed", on_changed)

        # Tooltip checkbox
        tooltip_checkbox = Gtk.CheckButton(label=_("Show tooltips on errors"))
        tooltip_checkbox.set_active(self.get_show_tooltip())
        tooltip_checkbox.set_tooltip_text(_("Show or hide tooltips when hovering over errors"))
        tooltip_checkbox.connect("toggled", self.on_toggle_tooltip)

        box.pack_start(Gtk.Label(label=_("LanguageTool Server:")), False, False, 0)
        box.pack_start(entry, False, False, 0)
        box.pack_start(Gtk.Label(label=_("Correction language:")), False, False, 0)
        box.pack_start(lang_combo, False, False, 0)
        box.pack_start(tooltip_checkbox, False, False, 0)
        box.show_all()
        return box
    
    def show_status_message(self, message):
        """Display a message in the Gedit status bar."""
        window = self.view.get_toplevel()
        if hasattr(window, "get_statusbar"):
            statusbar = window.get_statusbar()
            context_id = statusbar.get_context_id("LTplugin")
            statusbar.push(context_id, message)





