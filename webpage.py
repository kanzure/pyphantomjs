'''
  This file is part of the PyPhantomJS project.

  Copyright (C) 2011 James Roe <roejames12@hotmail.com>

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

from math import ceil, floor

from PyQt4.QtCore import pyqtProperty, pyqtSlot, pyqtSignal, Qt, QObject, \
                         QRect, QPoint, QUrl, QFileInfo, QDir, QSize, \
                         QSizeF, QByteArray, QEventLoop, QFile
from PyQt4.QtGui import QPalette, QDesktopServices, QPrinter, QImage, \
                        QPainter, QRegion, QApplication, qRgba
from PyQt4.QtWebKit import QWebSettings, QWebPage
from PyQt4.QtNetwork import QNetworkAccessManager, QNetworkRequest

from plugincontroller import Bunch, do_action


# Different defaults.
# OSX: 72, X11: 75(?), Windows: 96
pdf_dpi = 72


class CustomPage(QWebPage):
    def __init__(self, parent=None):
        QWebPage.__init__(self, parent)

        self.parent = parent
        self.m_userAgent = QWebPage.userAgentForUrl(self, QUrl())

        do_action('CustomPageInit', Bunch(locals()))

    def shouldInterruptJavaScript(self):
        QApplication.processEvents(QEventLoop.AllEvents, 42)
        return False

    def javaScriptAlert(self, originatingFrame, msg):
        self.parent.javaScriptAlertSent.emit(msg)

    def javaScriptConsoleMessage(self, message, lineNumber, sourceID):
        if sourceID:
            message = '%s:%d %s' % (sourceID, lineNumber, message)
        self.parent.javaScriptConsoleMessageSent.emit(message)

    def userAgentForUrl(self, url):
        return self.m_userAgent

    do_action('CustomPage', Bunch(locals()))


class WebPage(QObject):
    javaScriptAlertSent = pyqtSignal(str)
    javaScriptConsoleMessageSent = pyqtSignal(str)
    loadStatusChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)

        # variable declarations
        self.m_paperSize = {}
        self.m_clipRect = QRect()

        self.setObjectName('WebPage')
        self.m_webPage = CustomPage(self)
        self.m_mainFrame = self.m_webPage.mainFrame()

        self.m_webPage.loadFinished.connect(self.finish)

        # Start with transparent background
        palette = self.m_webPage.palette()
        palette.setBrush(QPalette.Base, Qt.transparent)
        self.m_webPage.setPalette(palette)

        # Page size does not need to take scrollbars into account
        self.m_webPage.mainFrame().setScrollBarPolicy(Qt.Horizontal, Qt.ScrollBarAlwaysOff)
        self.m_webPage.mainFrame().setScrollBarPolicy(Qt.Vertical, Qt.ScrollBarAlwaysOff)

        self.m_webPage.settings().setAttribute(QWebSettings.OfflineStorageDatabaseEnabled, True)
        self.m_webPage.settings().setOfflineStoragePath(QDesktopServices.storageLocation(QDesktopServices.DataLocation))
        self.m_webPage.settings().setAttribute(QWebSettings.LocalStorageDatabaseEnabled, True)
        self.m_webPage.settings().setAttribute(QWebSettings.FrameFlatteningEnabled, True)
        self.m_webPage.settings().setAttribute(QWebSettings.LocalStorageEnabled, True)
        self.m_webPage.settings().setLocalStoragePath(QDesktopServices.storageLocation(QDesktopServices.DataLocation))

        # Ensure we have a document.body.
        self.m_webPage.mainFrame().setHtml('<html><body></body></html>')

        self.m_webPage.setViewportSize(QSize(400, 300))

        do_action('WebPageInit', Bunch(locals()))

    def applySettings(self, defaults):
        opt = self.m_webPage.settings()

        opt.setAttribute(QWebSettings.AutoLoadImages, defaults['loadImages'])
        opt.setAttribute(QWebSettings.PluginsEnabled, defaults['loadPlugins'])
        if 'userAgent' in defaults:
            self.m_webPage.m_userAgent = defaults['userAgent']

    def finish(self, ok):
        status = 'success' if ok else 'fail'
        self.loadStatusChanged.emit(status)

    def mainFrame(self):
        return self.m_mainFrame

    def renderImage(self):
        frameRect = QRect(QPoint(0, 0), self.m_mainFrame.contentsSize())
        if not self.m_clipRect.isEmpty():
            frameRect = self.m_clipRect

        viewportSize = self.m_webPage.viewportSize()
        self.m_webPage.setViewportSize(self.m_mainFrame.contentsSize())

        image = QImage(frameRect.size(), QImage.Format_ARGB32)
        image.fill(qRgba(255, 255, 255, 0))

        painter = QPainter()

        # We use tiling approach to work-around Qt software rasterizer bug
        # when dealing with very large paint device.
        # See http://code.google.com/p/phantomjs/issues/detail?id=54.
        tileSize = 4096
        htiles = (image.width() + tileSize - 1) / tileSize
        vtiles = (image.height() + tileSize - 1) / tileSize
        for x in range(htiles):
            for y in range(vtiles):
                tileBuffer = QImage(tileSize, tileSize, QImage.Format_ARGB32)
                tileBuffer.fill(qRgba(255, 255, 255, 0))

                # Render the web page onto the small tile first
                painter.begin(tileBuffer)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setRenderHint(QPainter.TextAntialiasing, True)
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.translate(-frameRect.left(), -frameRect.top())
                painter.translate(-x * tileSize, -y * tileSize)
                self.m_mainFrame.render(painter, QRegion(frameRect))
                painter.end()

                # Copy the tile to the main buffer
                painter.begin(image)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.drawImage(x * tileSize, y * tileSize, tileBuffer)
                painter.end()

        self.m_webPage.setViewportSize(viewportSize)
        return image

    def renderPdf(self, fileName):
        p = QPrinter()
        p.setOutputFormat(QPrinter.PdfFormat)
        p.setOutputFileName(fileName)
        p.setResolution(pdf_dpi)
        paperSize = self.m_paperSize

        if not len(paperSize):
            pageSize = QSize(self.m_webPage.mainFrame().contentsSize())
            paperSize['width'] = str(pageSize.width()) + 'px'
            paperSize['height'] = str(pageSize.height()) + 'px'
            paperSize['border'] = '0px'

        if paperSize.get('width') and paperSize.get('height'):
            sizePt = QSizeF(ceil(self.stringToPointSize(paperSize['width'])),
                            ceil(self.stringToPointSize(paperSize['height'])))
            p.setPaperSize(sizePt, QPrinter.Point)
        elif 'format' in paperSize:
            orientation = QPrinter.Landscape if paperSize.get('orientation') and paperSize['orientation'].lower() == 'landscape' else QPrinter.Portrait
            orientation = QPrinter.Orientation(orientation)
            p.setOrientation(orientation)

            formats = {
                'A3': QPrinter.A3,
                'A4': QPrinter.A4,
                'A5': QPrinter.A5,
                'Legal': QPrinter.Legal,
                'Letter': QPrinter.Letter,
                'Tabloid': QPrinter.Tabloid
            }

            p.setPaperSize(QPrinter.A4) # fallback
            for format, size in formats.items():
                if format.lower() == paperSize['format'].lower():
                    p.setPaperSize(size)
                    break
        else:
            return False

        border = floor(self.stringToPointSize(paperSize['border'])) if paperSize.get('border') else 0
        p.setPageMargins(border, border, border, border, QPrinter.Point)

        self.m_webPage.mainFrame().print_(p)
        return True

    def setNetworkAccessManager(self, networkAccessManager):
        self.m_webPage.setNetworkAccessManager(networkAccessManager)

    def stringToPointSize(self, string):
        units = (
            ('mm', 72 / 25.4),
            ('cm', 72 / 2.54),
            ('in', 72.0),
            ('px', 72.0 / pdf_dpi / 2.54),
            ('', 72.0 / pdf_dpi / 2.54)
        )

        for unit, format in units:
            if string.endswith(unit):
                value = string.rstrip(unit)
                return float(value) * format
        return 0

    def userAgent(self):
        return self.m_webPage.m_userAgent

    ##
    # Properties and methods exposed to JavaScript
    ##

    @pyqtProperty('QVariantMap')
    def clipRect(self):
        result = {
            'width': self.m_clipRect.width(),
            'height': self.m_clipRect.height(),
            'top': self.m_clipRect.top(),
            'left': self.m_clipRect.left()
        }
        return result

    @clipRect.setter
    def clipRect(self, size):
        names = ('width', 'height', 'top', 'left')
        for item in names:
            try:
                globals()[item] = int(size[item])
                if globals()[item] < 0:
                    if item not in ('top', 'left'):
                        globals()[item] = 0
            except KeyError:
                globals()[item] = getattr(self.m_clipRect, item)()

        self.m_clipRect = QRect(left, top, width, height)

    @pyqtProperty(str)
    def content(self):
        return self.m_mainFrame.toHtml()

    @content.setter
    def content(self, content):
        self.m_mainFrame.setHtml(content)

    @pyqtSlot(str, result='QVariant')
    def evaluate(self, code):
        function = '(%s)()' % code
        return self.m_mainFrame.evaluateJavaScript(function)

    @pyqtSlot(str, str, 'QVariantMap')
    @pyqtSlot(str, 'QVariantMap', 'QVariantMap')
    def openUrl(self, address, op, settings):
        operation = op
        body = QByteArray()

        self.applySettings(settings)
        self.m_webPage.triggerAction(QWebPage.Stop)

        if type(op) is dict:
            operation = op.get('operation')
            body = QByteArray(op.get('data', ''))

        if operation == '':
            operation = 'get'

        networkOp = QNetworkAccessManager.CustomOperation
        operation = operation.lower()
        if operation == 'get':
            networkOp = QNetworkAccessManager.GetOperation
        elif operation == 'head':
            networkOp = QNetworkAccessManager.HeadOperation
        elif operation == 'put':
            networkOp = QNetworkAccessManager.PutOperation
        elif operation == 'post':
            networkOp = QNetworkAccessManager.PostOperation
        elif operation == 'delete':
            networkOp = QNetworkAccessManager.DeleteOperation

        if networkOp == QNetworkAccessManager.CustomOperation:
            self.m_mainFrame.evaluateJavaScript('console.error("Unknown network operation: %s");' % operation)
            return

        self.m_mainFrame.load(QNetworkRequest(QUrl(address)), networkOp, body)

    @pyqtProperty('QVariantMap')
    def paperSize(self):
        return self.m_paperSize

    @paperSize.setter
    def paperSize(self, size):
        self.m_paperSize = size

    @pyqtSlot(str, result=bool)
    def render(self, fileName):
        if self.m_mainFrame.contentsSize() == '':
            return False

        fileInfo = QFileInfo(fileName)
        path = QDir()
        path.mkpath(fileInfo.absolutePath())

        if fileName.lower().endswith('.pdf'):
            return self.renderPdf(fileName)

        image = self.renderImage()

        return image.save(fileName)

    @pyqtProperty('QVariantMap')
    def viewportSize(self):
        size = self.m_webPage.viewportSize()
        result = {
            'width': size.width(),
            'height': size.height()
        }
        return result

    @viewportSize.setter
    def viewportSize(self, size):
        names = ('width', 'height')
        for item in names:
            try:
                globals()[item] = int(size[item])
                if globals()[item] < 0:
                    globals()[item] = 0
            except KeyError:
                globals()[item] = getattr(self.m_webPage.viewportSize(), item)()

        self.m_webPage.setViewportSize(QSize(width, height))

    do_action('WebPage', Bunch(locals()))
