# flake8: noqa F403, F405
import sys
import threading
from cefpython3 import cefpython as cef
import platform
import werkzeug

from PyQt5.QtGui import *
from PyQt5.QtCore import *
from PyQt5.QtWidgets import *

WindowUtils = cef.WindowUtils()

# Platforms
WINDOWS = (platform.system() == "Windows")
LINUX = (platform.system() == "Linux")
MAC = (platform.system() == "Darwin")

# Configuration
# TODO remember this or calculate it?
WIDTH = 1024
HEIGHT = 768

# OS differences
# noinspection PyUnresolvedReferences
CefWidgetParent = QWidget
if LINUX:
    # noinspection PyUnresolvedReferences
    CefWidgetParent = QX11EmbedContainer


# TODO make this cleaner
# Document more
# rename?
def main():
    sys.excepthook = cef.ExceptHook  # To shutdown all CEF processes on error
    settings = {}
    if MAC:
        settings["external_message_pump"] = True

    cef.Initialize(settings)
    app = CefApplication(sys.argv)
    main_window = MainWindow()
    main_window.show()
    main_window.activateWindow()
    main_window.raise_()
    app.exec_()
    if not cef.GetAppSetting("external_message_pump"):
        app.stopTimer()
    del main_window  # Just to be safe, similarly to "del app"
    del app  # Must destroy app object before calling Shutdown
    cef.Shutdown()
    sys.exit(0)


class WorkerSignals(QObject):
    '''
    Defines the signals available from a running worker thread.
    Supported signals are:
    finished
    No data
    error
    `tuple` (exctype, value, traceback.format_exc() )
    result
    `object` data returned from processing, anything
    '''
    finished = pyqtSignal()
    error = pyqtSignal(tuple)
    result = pyqtSignal(tuple)

# TODO do I need to autodelete
# TODO how to make it a daemon?
class ServerRunWorker(QRunnable):
    def __init__(self, app, *args, **kwargs):
        super(ServerRunWorker, self).__init__()
        self.app = app

    @pyqtSlot()
    def run(self):
        host = "127.0.0.1"
        port = 8000
        debug = False
        # TODO check if thread should have access to self?
        self.app.run(host=host, debug=debug, port=port, threaded=True)

# noinspection PyUnresolvedReferences
class DataLoadWorker(QRunnable):
    def __init__(self, data, nav, *args, **kwargs):
        super(DataLoadWorker, self).__init__()
        self.data = data
        self.nav = nav
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self):
        from server.app.scanpy_engine.scanpy_engine import ScanpyEngine
        args = {
            "layout": "umap",
            "diffexp": "ttest",
            "max_category_items": 100,
            "diffexp_lfc_cutoff": 0.01,
            "obs_names": None,
            "var_names": None,
        }
        data_results = ScanpyEngine(self.data, args)
        self.signals.result.emit((data_results, self.nav))
        self.signals.finished.emit()

# noinspection PyUnresolvedReferences
class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__(None)
        self.threadpool = QThreadPool()
        self.cef_widget = None
        self.navigation_bar = None
        self.server = cellxgeneServer(self.threadpool)
        self.server.setupApp()
        self.setWindowTitle("cellxgene")

        # Strong focus - accepts focus by tab & click
        self.setFocusPolicy(Qt.StrongFocus)
        self.setupLayout()


    def setupLayout(self):
        self.resize(WIDTH, HEIGHT)
        self.cef_widget = CefWidget(self)
        # TODO rename navigation bar
        self.navigation_bar = LoadWidget(self.cef_widget)
        layout = QGridLayout()
        layout.addWidget(self.cef_widget, 1, 0)
        layout.addWidget(self.navigation_bar, 0, 0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)
        frame = QFrame()
        frame.setLayout(layout)
        self.setCentralWidget(frame)

        if WINDOWS:
            # On Windows with PyQt5 main window must be shown first
            # before CEF browser is embedded, otherwise window is
            # not resized and application hangs during resize.
            self.show()

        # Browser can be embedded only after layout was set up
        self.cef_widget.embedBrowser()

        if LINUX:
            # On Linux with PyQt5 the QX11EmbedContainer widget is
            # no more available. An equivalent in Qt5 is to create
            # a hidden window, embed CEF browser in it and then
            # create a container for that hidden window and replace
            # cef widget in the layout with the container.
            self.container = QWidget.createWindowContainer(
                self.cef_widget.hidden_window, parent=self)
            layout.addWidget(self.container, 1, 0)

    def closeEvent(self, event):
        # Close browser (force=True) and free CEF reference
        if self.cef_widget.browser:
            self.cef_widget.browser.CloseBrowser(True)
            self.clear_browser_references()

    def clear_browser_references(self):
        # Clear browser references that you keep anywhere in your
        # code. All references must be cleared for CEF to shutdown cleanly.
        self.cef_widget.browser = None



class cellxgeneServer():
    def __init__(self, threadPool):
        self.app = None
        self.threadpool = threadPool

    def setupApp(self):
        from server.app.app import Server
        server = Server()
        self.app = server.create_app()
        self.app.config.update(DATASET_TITLE="DEMO!")
        # Technically a race condition
        self.runServer()

    def runServer(self):
        worker = ServerRunWorker(self.app)
        self.threadpool.start(worker)

    def loadData(self, file_name, nav):
        worker = DataLoadWorker(file_name, nav)
        worker.signals.result.connect(self.attachData)
        self.threadpool.start(worker)

    def attachData(self, results):
        data = results[0]
        nav = results[1]
        self.app.data = data
        nav()


# noinspection PyUnresolvedReferences
class CefWidget(CefWidgetParent):
    def __init__(self, parent=None):
        super(CefWidget, self).__init__(parent)
        self.parent = parent
        self.browser = None
        # TODO test without this on linux
        self.hidden_window = None  # Required for PyQt5 on Linux
        self.show()

    def focusInEvent(self, event):
        # This event seems to never get called on Linux, as CEF is
        # stealing all focus due to Issue #284.
        if self.browser:
            if WINDOWS:
                WindowUtils.OnSetFocus(self.getHandle(), 0, 0, 0)
            self.browser.SetFocus(True)

    def focusOutEvent(self, event):
        # This event seems to never get called on Linux, as CEF is
        # stealing all focus due to Issue #284.
        if self.browser:
            self.browser.SetFocus(False)

    # TODO when does this happen?
    def embedBrowser(self):
        if LINUX:
            self.hidden_window = QWindow()
        window_info = cef.WindowInfo()
        rect = [0, 0, self.width(), self.height()]
        window_info.SetAsChild(self.getHandle(), rect)
        # TODO better splash
        self.browser = cef.CreateBrowserSync(window_info,
                                             url="http://google.com")

    def getHandle(self):
        if self.hidden_window:
            # PyQt5 on Linux
            return int(self.hidden_window.winId())
        else:
            return int(self.winId())

    def moveEvent(self, _):
        self.x = 0
        self.y = 0
        if self.browser:
            if WINDOWS:
                WindowUtils.OnSize(self.getHandle(), 0, 0, 0)
            elif LINUX:
                self.browser.SetBounds(self.x, self.y,
                                       self.width(), self.height())
            self.browser.NotifyMoveOrResizeStarted()

    def resizeEvent(self, event):
        size = event.size()
        if self.browser:
            if WINDOWS:
                WindowUtils.OnSize(self.getHandle(), 0, 0, 0)
            elif LINUX:
                self.browser.SetBounds(self.x, self.y,
                                       size.width(), size.height())
            self.browser.NotifyMoveOrResizeStarted()


# For Windows -- transfer event loop control on timer
# noinspection PyUnresolvedReferences
class CefApplication(QApplication):
    def __init__(self, args):
        super(CefApplication, self).__init__(args)
        if not cef.GetAppSetting("external_message_pump"):
            self.timer = self.createTimer()

    def createTimer(self):
        timer = QTimer()
        timer.timeout.connect(self.onTimer)
        timer.start(10)
        return timer

    def onTimer(self):
        cef.MessageLoopWork()

    def stopTimer(self):
        # Stop the timer after Qt's message loop has ended
        self.timer.stop()

# noinspection PyUnresolvedReferences
class LoadWidget(QFrame):
    def __init__(self, cef_widget):
        super(LoadWidget, self).__init__()
        self.cef_widget = cef_widget

        # Init layout
        layout = QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.load = QPushButton("load")
        self.load.clicked.connect(self.onLoad)
        layout.addWidget(self.load, 0, 0)

        # Layout
        self.setLayout(layout)

    def onLoad(self):
        options = QFileDialog.Options()
        # options |= QFileDialog.DontUseNativeDialog
        fileName, _ = QFileDialog.getOpenFileName(self,
                                                  "Open H5AD File", "", "H5AD Files (*.h5ad)", options=options)
        if fileName:
            # TODO handle this better
            # TODO thread this
            self.cef_widget.parent.server.loadData(fileName, self.navigateToLocation)
            # TODO instead create cef_widget


    def navigateToLocation(self):
        self.cef_widget.browser.Navigate("http://localhost:8000/")


    def createButton(self, name):
        return QPushButton(name)


if __name__ == '__main__':
    main()