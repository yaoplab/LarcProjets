import os
from .database import db
from .logger import log
from .paths import larc_root

# Clés de chemin : une valeur relative (en base) est résolue depuis larc_root().
_PATH_KEYS = ('photos_dir', 'photos_cache_dir', 'staff_photos_dir')


class AppConfig:
    CONFIG_TABLE = 'larcauth_config'

    DEFAULTS = {
        'photos_dir': os.path.normpath(
            str(larc_root() / 'LarcSuperviseur' / 'photos')),
        'photos_cache_dir': os.path.normpath(
            str(larc_root() / 'LarcSuperviseur' / 'photos' / 'cache')),
        'staff_photos_dir': os.path.normpath(
            str(larc_root() / 'LarcRH' / 'photos')),
    }

    def __init__(self):
        self._cache = dict(self.DEFAULTS)
        self._loaded = False

    def load(self):
        if not db.is_server_connected:
            return
        conn = db.server_conn
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT key, value FROM {self.CONFIG_TABLE}")
            for key, value in cur.fetchall():
                self._cache[key] = value
            self._loaded = True
            log(f"AppConfig: charge {len(self._cache)} clefs depuis PostgreSQL")
        except Exception as e:
            from larccommon.error_reporting import get_reporter
            get_reporter().report_exception()
            log(f"AppConfig: echec chargement ({e}), fallback valeurs par defaut")

    def get(self, key: str, default=None):
        if not self._loaded:
            self.load()
        value = self._cache.get(key, default)
        if key in _PATH_KEYS and isinstance(value, str) and value \
                and not os.path.isabs(value):
            # Chemin relatif en base : relatif à la racine du monorepo.
            value = os.path.normpath(str(larc_root() / value))
        return value


app_config = AppConfig()
