"""Independent Qt status window, so blocked rendering cannot freeze feedback."""
from contextlib import contextmanager
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
from time import monotonic


def media_directory():
    checkout = Path(__file__).resolve().parent.parent / 'media'
    return checkout if checkout.is_dir() else Path(sys.prefix) / 'share/steposcope/media'


class BusyIndicator:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.process = None
        self.sequence = 0

    def send(self, event, **values):
        if not self.enabled:
            return
        try:
            if self.process is None:
                self.process = subprocess.Popen(
                    [sys.executable, '-m', 'step_explorer.busy_indicator', str(media_directory())],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                )
            message = dict(event=event, time=monotonic(), **values)
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.enabled = False  # Feedback failure must not interrupt model processing.

    def heartbeat(self):
        self.send('heartbeat')

    @contextmanager
    def operation(self, label):
        self.sequence += 1
        key = f'operation-{self.sequence}'
        self.send('begin', key=key, label=label)
        try:
            yield
        except BaseException:
            self.send('end', key=key, success=False)
            raise
        else:
            self.send('end', key=key, success=True)

    def close(self):
        self.enabled = False
        if self.process is not None and self.process.stdin is not None:
            try:
                self.process.stdin.close()  # EOF closes the helper, including after main-process death.
            except OSError:
                pass


def busy_operation(label):
    from functools import wraps

    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            indicator = getattr(self, '_busy_indicator', None)
            if indicator is None:
                return function(self, *args, **kwargs)
            with indicator.operation(label):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate


class ActivityState:
    """No Qt: explicit long operations plus a fallback UI-heartbeat watchdog."""
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.last_heartbeat = clock()
        self.activities = {}
        self.waiting = False
        self.result_until = 0
        self.failed = False

    def receive(self, message):
        now = self.clock()
        event = message['event']
        if event == 'begin':
            if not self.activities:
                self.result_until = 0
                self.failed = False
            self.activities[message['key']] = (message['time'], message['label'])
        elif event == 'end':
            activity = self.activities.pop(message['key'], None)
            self.failed |= not message.get('success', True)
            if not self.activities and (self.waiting or (activity and now - activity[0] >= 5)):
                self.waiting = False
                self.result_until = now + 4
        elif event == 'heartbeat':
            if self.waiting and not self.activities:
                self.waiting = False
                self.result_until = now + 4
            self.last_heartbeat = message['time']

    def display(self):
        now = self.clock()
        if self.activities:
            start, label = min(self.activities.values())
            if now - start >= 5:
                self.waiting = True
                return 'waiting', label
            return None
        if now < self.result_until:
            return ('error', 'Operation stopped') if self.failed else ('result', 'Ready')
        if now - self.last_heartbeat >= 5:
            self.waiting = True
            return 'waiting', 'Processing…'
        return None


def main():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    directory = Path(sys.argv[1])
    window = QWidget(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
    window.setWindowTitle('StepOScope — processing status')
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window.setStyleSheet('background: #20242a; color: white;')
    layout = QVBoxLayout(window)
    picture, caption = QLabel(), QLabel()
    picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(picture)
    layout.addWidget(caption)
    images = {
        name: QPixmap(str(directory / f'{name}.png')).scaled(
            280, 320, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        ) for name in ('waiting', 'result')
    }
    messages = queue.Queue()

    def read_messages():
        try:
            for line in sys.stdin:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            messages.put(None)

    threading.Thread(target=read_messages, daemon=True).start()
    state = ActivityState()
    previous = None

    def update():
        nonlocal previous
        while not messages.empty():
            message = messages.get_nowait()
            if message is None:
                app.quit()
                return
            state.receive(message)
        display = state.display()
        if display == previous:
            return
        previous = display
        if display is None:
            window.hide()
            return
        kind, text = display
        picture.setPixmap(images['result' if kind == 'result' else 'waiting'])
        caption.setText(text)
        window.adjustSize()
        screen = app.primaryScreen().availableGeometry()
        window.move(screen.right() - window.width() - 24, screen.bottom() - window.height() - 40)
        window.show()

    timer = QTimer()
    timer.timeout.connect(update)
    timer.start(100)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
