import gzip, logging, os, re
from datetime import date

_LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
_FILE_RE = re.compile(r"system-(\d{4}-\d{2}-\d{2})\.log(\.gz)?")
_RETENTION_DAYS = 30


def _compress_file(path: str) -> None:
    with open(path, "rb") as f_in, gzip.open(path + ".gz", "wb") as f_out:
        f_out.write(f_in.read())
    os.remove(path)


def _cleanup() -> None:
    today = date.today()
    for name in os.listdir(_LOG_DIR):
        m = _FILE_RE.match(name)
        if not m:
            continue
        path = os.path.join(_LOG_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if name.endswith(".gz"):
            if (today - d).days > _RETENTION_DAYS:
                os.remove(path)
        elif d < today:
            _compress_file(path)


class _DailyGzHandler(logging.Handler):
    def __init__(self, log_dir: str):
        super().__init__()
        self._log_dir = log_dir
        self._stream = None
        self._day = None

    def emit(self, record):
        try:
            self._ensure_stream()
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def _ensure_stream(self):
        day = date.today().isoformat()
        if self._stream is not None and self._day == day:
            return
        if self._stream is not None:
            self._stream.close()
            _compress_file(self._stream.name)
            self._stream = None
        self._day = day
        self._stream = open(os.path.join(self._log_dir, f"system-{day}.log"), "a", encoding="utf-8")


def get_logger(name: str, console: bool = False) -> logging.Logger:
    log = logging.getLogger("laoxi." + name)
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    os.makedirs(_LOG_DIR, exist_ok=True)
    _cleanup()
    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    fh = _DailyGzHandler(_LOG_DIR)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)
    return log
