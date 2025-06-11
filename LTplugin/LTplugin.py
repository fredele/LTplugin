from gi.repository import GObject, Gtk, Gedit, PeasGtk, Gio, GLib, Pango
import urllib.request
import urllib.parse
import json
import threading
import configparser
import os

class LTCheckAppActivatable(GObject.Object, Gedit.AppActivatable):
    app = GObject.property(type=Gedit.App)
    __gtype_name__ = "LTCheckAppActivatable"

    def do_activate(self):
        # Ajoute l'entrée dans le menu Outils
        self.menu_ext = self.extend_menu("tools-section")
        item = Gio.MenuItem.new("Vérification grammaticale", 'win.toggle_check')
        self.menu_ext.append_menu_item(item)

    def do_deactivate(self):
        # Nettoyage du menu
        self.menu_ext = None

class LTCheckWindowActivatable(GObject.Object, Gedit.ViewActivatable, PeasGtk.Configurable):
    view = GObject.Property(type=Gedit.View)
    __gtype_name__ = "LTCheckWindowActivatable"
    
    
    def get_server_url(self):
      config = configparser.ConfigParser()
      config_path = os.path.join(os.path.dirname(__file__), "config.ini")
      config.read(config_path)
      return config.get("settings", "server_url", fallback="http://localhost:8081")
    
    def do_activate(self):
        self.buffer = self.view.get_buffer()
        self.errors = []
        self.enabled = False
        self.interval = 3000  # 3 secondes
        self.server_url = self.get_server_url()
        print(self.server_url)
        self.view.set_has_tooltip(True)
        self.view.connect("query-tooltip", self.on_query_tooltip)

        self.ensure_tag()
        self.setup_action()
        self.start_timer()

    def do_deactivate(self):
        self.enabled = False

    def setup_action(self):
        action = Gio.SimpleAction.new_stateful("toggle_check", None, GLib.Variant.new_boolean(False))
        action.connect("activate", self.on_toggle)
        self.view.get_toplevel().add_action(action)

    def on_toggle(self, action, param):
        self.enabled = not self.enabled
        action.set_state(GLib.Variant.new_boolean(self.enabled))

    def ensure_tag(self):
        if not self.buffer.get_tag_table().lookup("highlight"):
            self.tag = self.buffer.create_tag("highlight", underline=Pango.Underline.ERROR)

    def start_timer(self):
        def periodic():
            if self.enabled:
                self.check_text()
            else:
              tag = self.buffer.get_tag_table().lookup("highlight")
              if tag:
                  self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())
              self.errors = []
            return True
        GLib.timeout_add(self.interval, periodic)

    def check_text(self):
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)

        def worker():
            try:
                query = urllib.parse.urlencode({"language": "auto", "text": text})
                with urllib.request.urlopen(f"{self.server_url}?{query}") as response:
                    result = json.loads(response.read().decode())

                def apply_results():
                    self.errors.clear()
                    tag = self.buffer.get_tag_table().lookup("highlight")
                    self.buffer.remove_tag(tag, self.buffer.get_start_iter(), self.buffer.get_end_iter())

                    for match in result.get("matches", []):
                        offset = match["offset"]
                        length = match["length"]
                        message = match["message"]
                        replacements = match.get("replacements", [])
                        start_iter = self.buffer.get_iter_at_offset(offset)
                        end_iter = self.buffer.get_iter_at_offset(offset + length)
                        self.buffer.apply_tag(self.tag, start_iter, end_iter)
                        self.errors.append((offset, offset + length, message, replacements))
                    return False

                GLib.idle_add(apply_results)

            except Exception as e:
                print(f"Erreur de requête : {e}")

        threading.Thread(target=worker, daemon=True).start()

    def on_query_tooltip(self, widget, x, y, keyboard_mode, tooltip):
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, x, y)
        success, iter_at_pos = self.view.get_iter_at_location(bx, by)
        if not success:
            return False

        word_start = iter_at_pos.copy()
        word_start.backward_word_start()
        offset = word_start.get_offset()

        for start, end, message, replacements in self.errors:
            if start <= offset < end:
                tooltip.set_text(message + "\nSuggestions : " + ", ".join(r["value"] for r in replacements))
                return True

        return False

    def do_create_configure_widget(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=12)
        entry = Gtk.Entry()
        self.server_url = self.get_server_url()
        print(self.server_url)
        entry.set_text(self.server_url)
        entry.set_placeholder_text("LanguageTool Server")

        def on_changed(widget):
            self.server_url = widget.get_text()
            config.set("settings", "server_url", entry.get_text())
            with open(config_path, "w") as f:
                config.write(f)

        entry.connect("changed", on_changed)
        box.pack_start(Gtk.Label(label="LanguageTool Server :"), False, False, 0)
        box.pack_start(entry, False, False, 0)
        box.show_all()
        return box



