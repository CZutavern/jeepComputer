from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel


class PageFader(QLabel):
    """
    Cross-fade for a QStackedWidget: a snapshot of the old page is laid on
    top of the new one and faded out. Touches go straight through to the
    new page, so it never feels slower than an instant switch.
    """

    def __init__(self, stack, duration_ms=180):
        super().__init__(stack)
        self.stack = stack
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)

        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(duration_ms)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self.hide)

    def switch(self, index):
        if index == self.stack.currentIndex() or not self.stack.isVisible():
            self.stack.setCurrentIndex(index)
            return

        self.anim.stop()
        self.setPixmap(self.stack.currentWidget().grab())
        self.setGeometry(self.stack.rect())
        self.effect.setOpacity(1.0)
        self.show()
        self.raise_()

        self.stack.setCurrentIndex(index)
        self.anim.start()
