#!/usr/bin/python3
import os
import os.path
import shutil
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from picamera2 import Picamera2, Platform
from picamera2.previews.qt import QGlPicamera2
from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMainWindow,
                             QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

# finding ctt folder
ctt_directory = os.path.join(os.path.expanduser("~"), 'libcamera/utils/raspberrypi/ctt')
sys.path.insert(1, ctt_directory)
from ctt import Camera, run_ctt
from ctt_image_load import dng_load_image
from ctt_macbeth_locator import find_macbeth, get_macbeth_chart


# doesnt let runtime warning print
def fxn():
    warnings.warn("runtime", RuntimeWarning)


# Configuring the camera
# sorting functions for camera modes
def sortModeArea(d):
    return d['crop_limits'][-1] * d['crop_limits'][-2]


def sortBitDepth(d):
    return d['bit_depth']


picam2 = Picamera2()
candidate_modes = []


for mode in picam2.sensor_modes:
    if mode['crop_limits'].count(0) > 1:
        candidate_modes.append(mode)


if len(candidate_modes) == 1:
    mode = candidate_modes[0]
else:
    candidate_modes.sort(key=sortModeArea)
    tuple_ful = candidate_modes[-1]['size']
    tuple_half = tuple(ti / 2 for ti in tuple_ful)

    half_candidates = [mode for mode in candidate_modes if mode['size'] == tuple_half]
    full_candidates = [mode for mode in candidate_modes if mode['size'] == tuple_ful]

    if half_candidates:
        half_candidates.sort(key=sortBitDepth)
        mode = half_candidates[-1]
    else:
        full_candidates.sort(key=sortBitDepth)
        mode = full_candidates[-1]
main = {"size": (800, 600)}
sensor = {'output_size': mode['size'], 'bit_depth': mode['bit_depth']}
config = picam2.create_preview_configuration(main=main, sensor=sensor)
picam2.configure(config)


# Function that returns true if it can detect macbeth plot
def check(filename, target):
    Cam = Camera("imx.json", json=json_template)
    Img = dng_load_image(Cam, filename)

    av_chan = (np.mean(np.array(Img.channels), axis=0) / (2 ** 16))
    av_val = np.mean(av_chan)

    if av_val < Img.blacklevel_16 / (2**16) + 1 / 64:
        return False
    else:
        macbeth = find_macbeth(Cam, av_chan, mac_config=(0, 0))
    if macbeth is None:
        return False
    else:
        return True


# add camera name to this folder that is default
index_photo = 0
start = False
list_of_index = []
for f in os.listdir(os.path.expanduser("~")):
    if picam2.global_camera_info()[0]["Model"] in f:
        if "_" in f:
            try:  # in case of e.g. "imx708_wide" where we don't have a number after the "_"
                list_of_index.append(int(f[f.rindex("_") + 1:]))
            except Exception:
                pass
        index_photo += 1
if index_photo == 0:
    folder_directory = os.path.join(os.path.expanduser("~"), picam2.global_camera_info()[0]["Model"])
elif list_of_index == []:
    folder_directory = os.path.join(os.path.expanduser("~"), picam2.global_camera_info()[0]["Model"] + "_" + str(index_photo))
else:
    list_of_index.sort()
    index_photo = list_of_index[-1] + 1
    folder_directory = os.path.join(os.path.expanduser("~"), picam2.global_camera_info()[0]["Model"] + "_" + str(index_photo))

# what pi are we working on?
if Picamera2.platform == Platform.PISP:
    target = "pi5"
    from ctt_pisp import json_template
else:
    target = "pi4"
    from ctt_vc4 import json_template


# checks if sting is a valid path to a folder
def valid_path(s):
    if (s[0] != "/" or s[-1] == "/"):
        return False
    else:
        return True


# create the screen that shades if it can se a macbeth plote clos to camera
# macbeth reference data
Cam = Camera("dummy_name.json", json=json_template)
s = os.path.join(Cam.path, 'ctt_ref.pgm')
ref = cv2.imread(s, flags=cv2.IMREAD_GRAYSCALE)
ref_w = 120
ref_h = 80
rc1 = (0, 0)
rc2 = (0, ref_h)
rc3 = (ref_w, ref_h)
rc4 = (ref_w, 0)
ref_corns = np.array((rc1, rc2, rc3, rc4), np.float32)
ref_data = (ref, ref_w, ref_h, ref_corns)

# function that checks for macbeth close to camera


def my_find_macbeth(img):
    warnings.simplefilter("ignore")
    fxn()
    cor, mac, coords, msg = get_macbeth_chart(img, ref_data)
    if cor < 0.75:
        a = 2
        img_br = cv2.convertScaleAbs(img, alpha=a, beta=0)
        cor_b, mac_b, coords_b, msg_b = get_macbeth_chart(img_br, ref_data)
        if cor_b > cor:
            cor, _, coords, _ = cor_b, mac_b, coords_b, msg_b
    if cor < 0.75:
        a = 4
        img_br = cv2.convertScaleAbs(img, alpha=a, beta=0)
        cor_b, mac_b, coords_b, msg_b = get_macbeth_chart(img_br, ref_data)
        if cor_b > cor:
            cor, _, coords, _ = cor_b, mac_b, coords_b, msg_b
    if cor > 0.5:
        return (cor, coords)
    else:
        return (0, None)

# future parameters


pool = ThreadPoolExecutor(max_workers=4)
overlay_active = False
busy = False

# Class that makes the wisget itself that will enclose macbeth if it sees it


class MacbethWindow(QWidget):
    done_signal = pyqtSignal()

    def __init__(self, *args, **kwargs):
        super(QWidget, self).__init__(*args, **kwargs)
        self.qpicamera2 = QGlPicamera2(picam2, width=800, height=600, keep_ar=True, bg_colour=(236, 236, 236))
        self.qpicamera2.done_signal.connect(self.capture_done)

        self.window = QWidget()
        self.layout_h = QHBoxLayout()
        self.layout_h.addWidget(self.qpicamera2, 80)
        self.window.resize(1200, 600)
        self.window.setLayout(self.layout_h)

        QTimer.singleShot(0, self.request_frame)
        picam2.start()

        self.count = 0
        self.futures = []
        # to make the green macbeth frame more stabile, we remove it when not dettected for 10 periods
        self.macbeth_glitch = 0

    def request_frame(self):
        if not busy:
            picam2.capture_array(signal_function=self.qpicamera2.signal_done)

    def signal_done(self, job):
        self.done_signal.emit()

    def capture_done(self, job):
        global overlay_active
        array = job.get_result()
        if self.count < 1:
            # add another future
            future = pool.submit(my_find_macbeth, array)
            self.futures.append(future)
            self.count += 1

        if self.futures:
            if self.futures[0].running() is False:
                cor, macbeth = self.futures[0].result()
                self.futures.pop(0)
                self.count -= 1
                # OVERLAY
                if cor != 0:
                    overlay_active = True
                    self.macbeth_glitch = 0
                    h = array.shape[0]
                    w = array.shape[1]
                    m = macbeth[0].astype(np.int64)
                    for i in range(4):
                        m[0][i][0] = int(round(m[0][i][0] * 800 / w))
                        m[0][i][1] = int(round(m[0][i][1] * 800 / h))

                    overlay = np.zeros((800, 800, 4), dtype=np.uint8)
                    cv2.polylines(img=overlay, pts=m, isClosed=True, color=(0, 255, 0, 100), thickness=5)
                    try:
                        self.qpicamera2.set_overlay(overlay)
                    except RuntimeError:
                        pass
                elif self.macbeth_glitch > 10:
                    overlay_active = False
                    self.filter_machb = 0
                    overlay = np.zeros((800, 800, 4), dtype=np.uint8)
                    try:
                        self.qpicamera2.set_overlay(overlay)
                    except RuntimeError:
                        pass
                else:
                    self.macbeth_glitch += 1

        QTimer.singleShot(1, self.request_frame)


# Create an app that asks for file directory, with the suggested name
class FirstWindow(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        self.folder_directory = QLineEdit()
        self.folder_directory.setText(folder_directory)
        self.label = QLabel()
        self.label.setText("Button will be grayed out for incorrect, or already exsting, folder paths.")
        self.button_initialise = QPushButton("Next")
        self.button_initialise.clicked.connect(self.push_button)
        layout.addWidget(self.label)
        layout.addWidget(self.folder_directory)
        layout.addWidget(self.button_initialise)
        self.setLayout(layout)
        self._timer = QTimer()
        self._timer.timeout.connect(self.onTimeout)
        self._timer.start()
        self.show()

    # grey out if input is not path to folder
    def onTimeout(self):
        if self.folder_directory.text() == "":
            self.button_initialise.setDisabled(False)
            return 0
        # already exists folder with this name
        elif os.path.exists(self.folder_directory.text()) is True:
            self.button_initialise.setDisabled(True)
        elif valid_path(self.folder_directory.text()) is False:
            self.button_initialise.setDisabled(True)
        else:
            self.button_initialise.setDisabled(False)

    def push_button(self):
        global folder_directory, start
        start = True
        if self.folder_directory.text() == "":
            if os.path.exists(folder_directory):
                shutil.rmtree(folder_directory)
            os.makedirs(folder_directory)
        else:
            os.makedirs(self.folder_directory.text())
            folder_directory = self.folder_directory.text()
        self.close()


# Creating the main window
class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.title = "Tuning Application"
        self.left = 0
        self.top = 0
        self.width = 1400
        self.height = 600
        self.setWindowTitle(self.title)
        self.setGeometry(self.left, self.top, self.width, self.height)

        self.tab_widget = MyTabWidget(self)
        self.setCentralWidget(self.tab_widget)

        self.setStyleSheet("background-color : rgb(236,236,236);")

        self.show()


# Creating tab widgets

class MyTabWidget(QWidget):
    done_signal = pyqtSignal()

    def __init__(self, parent):
        super(QWidget, self).__init__(parent)
        self.layout = QHBoxLayout(self)
        self.layout1 = QHBoxLayout(self)

        # Initialize tab screen
        self.tabs = QTabWidget()
        self.tab1 = QWidget()
        self.tab2 = QWidget()
        self.tab3 = QWidget()

        # Add tabs
        self.tabs.addTab(self.tab1, "Macbeth")
        self.tabs.addTab(self.tab2, "Lens shading")
        self.tabs.addTab(self.tab3, "Cac")

        # Create the two necessary buttons for each tab
        # TAB 1
        self.button_tab1 = QPushButton("Click to capture Photo")
        self.button_tab1_1 = QPushButton("Done")
        self.button_tab1.clicked.connect(self.on_button_clicked)
        self.button_tab1_1.clicked.connect(self.on_button1_clicked)

        # TAB 2
        self.button_tab2 = QPushButton("Click to capture Photo")
        self.button_tab2_1 = QPushButton("Done")
        self.button_tab2.clicked.connect(self.on_button_clicked)
        self.button_tab2_1.clicked.connect(self.on_button1_clicked)

        # TAB 3
        self.button_tab3 = QPushButton("Click to capture Photo")
        self.button_tab3_1 = QPushButton("Done")
        self.button_tab3.clicked.connect(self.on_button_clicked)
        self.button_tab3_1.clicked.connect(self.on_button1_clicked)

        # Create the input lux and temperature lines
        # TAB 1
        self.title1 = QLabel()
        self.title1.setText("Pictures taken")
        self.label_temperature = QLabel()
        self.label_temperature.setText("Temperature")
        self.label_lux = QLabel()
        self.label_lux.setText("Lux")
        self.lux_tab1 = QLineEdit()
        self.lux_tab1.setPlaceholderText("lux")
        self.temperature_tab1 = QLineEdit()
        self.temperature_tab1.setPlaceholderText("temperature")

        # TAB 2
        self.title2 = QLabel()
        self.title2.setText("Pictures taken")
        self.label_temperature1 = QLabel()
        self.label_temperature1.setText("Temperature")
        self.temperature_tab2 = QLineEdit()
        self.temperature_tab2.setPlaceholderText("temperature")

        # Create macbeth and shading lists for TAB 1 and TAB 2
        self.listWidget_macbeth = QListWidget()
        self.listWidget_shading = QListWidget()

        # Create TAB 1
        self.tab1.layout = QVBoxLayout(self)
        self.tab1.layout.addWidget(self.title1)
        self.tab1.layout.addWidget(self.listWidget_macbeth)
        self.tab1.layout.addWidget(self.label_temperature)
        self.tab1.layout.addWidget(self.temperature_tab1)
        self.tab1.layout.addWidget(self.label_lux)
        self.tab1.layout.addWidget(self.lux_tab1)
        self.tab1.layout.addWidget(self.button_tab1)
        self.tab1.layout.addWidget(self.button_tab1_1)
        self.tab1.setLayout(self.tab1.layout)

        # Create TAB 2
        self.tab2.layout = QVBoxLayout(self)
        self.tab2.layout.addWidget(self.title2)
        self.tab2.layout.addWidget(self.listWidget_shading)
        self.tab2.layout.addWidget(self.label_temperature1)
        self.tab2.layout.addWidget(self.temperature_tab2)
        self.tab2.layout.addWidget(self.button_tab2)
        self.tab2.layout.addWidget(self.button_tab2_1)
        self.tab2.setLayout(self.tab2.layout)

        # Create TAB 3
        self.tab3.layout = QVBoxLayout(self)
        self.tab3.layout.addWidget(self.button_tab3)
        self.tab3.layout.addWidget(self.button_tab3_1)
        self.tab3.setLayout(self.tab3.layout)

        # connecting the camera to layout outside tabs
        self.qpicamera2 = MacbethWindow()
        self.layout.addWidget(self.qpicamera2.window, 80)
        self.qpicamera2.done_signal.connect(self.capture_done)
        picam2.start()

        # Add tabs to widget
        self.layout1.addWidget(self.tabs)
        self.layout.addLayout(self.layout1, 40)

        # Show layot in window
        self.setLayout(self.layout)

        # Updates whetehr input is adequate live time
        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.onTimeout)
        self._timer.start()

        # To know whether at capture image there is a green box on the screen
        self.overlay_active = False

        # lits containgin machbeth temeratures and asic temperature values alerady used
        self.macbeth_used = []
        self.alsc_used = []
        self.cac_used = 0
        self.macbeth_bool = False
        self.list_of_files = []

    # grey out if input is not in good form
    def onTimeout(self):
        if self.macbeth_bool:
            self.button_tab1.setDisabled(True)
        elif self.temperature_tab1.text().isdigit() and self.lux_tab1.text().isdigit():
            self.button_tab1.setDisabled(False)
        else:
            self.button_tab1.setDisabled(True)

        # lens shading tab
        if self.temperature_tab2.text().isdigit():
            self.button_tab2.setDisabled(False)
        else:
            self.button_tab2.setDisabled(True)

        if len(self.macbeth_used) < 2 or len(self.alsc_used) == 0 or self.macbeth_bool:
            self.button_tab1_1.setDisabled(True)
            self.button_tab2_1.setDisabled(True)
            self.button_tab3_1.setDisabled(True)
        else:
            self.button_tab1_1.setDisabled(False)
            self.button_tab2_1.setDisabled(False)
            self.button_tab3_1.setDisabled(False)

    # Creating the function that connects to button click
    def on_button_clicked(self):

        global folder_directory, busy
        # figure out which tab it is
        busy = True
        self.overlay_active = overlay_active
        if self.tabs.currentIndex() == 0 and not self.macbeth_bool:
            if self.temperature_tab1.text() in self.macbeth_used:
                msg = QMessageBox()
                msg.setText("Are you sure you want to overwrite image for this temperature?")
                msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
                ret = msg.exec()
                if ret == QMessageBox.Ok:
                    # macbeth procedure
                    self.button_tab1.setText("Wait while detecting chart")
                    self.button_tab1.setDisabled(True)
                    temperature_value = self.temperature_tab1.text()
                    lux_value = self.lux_tab1.text()
                    self.macbeth_bool = True
                    self.macbeth_used.append(temperature_value)
                    # now we know that both of the values are intergers and macbeth plot is suppost to be in the picture
                    filename = folder_directory + "/" + temperature_value + "K_" + lux_value + "L" + ".dng"
                    self.list_of_files.append(temperature_value + "K_" + lux_value + "L" + ".dng")
                    cfg = picam2.create_still_configuration()
                    picam2.switch_mode_and_capture_file(cfg, filename, signal_function=self.qpicamera2.signal_done, name="raw")
                else:
                    pass
            else:
                # macbeth procedure
                self.button_tab1.setText("Wait while detecting chart")
                self.button_tab1.setDisabled(True)
                temperature_value = self.temperature_tab1.text()
                lux_value = self.lux_tab1.text()
                self.macbeth_bool = True
                self.macbeth_used.append(temperature_value)
                # now we know that both of the values are intergers and macbeth plot is suppost to be in the picture
                filename = folder_directory + "/" + temperature_value + "K_" + lux_value + "L" + ".dng"
                self.list_of_files.append(temperature_value + "K_" + lux_value + "L" + ".dng")
                cfg = picam2.create_still_configuration()
                picam2.switch_mode_and_capture_file(cfg, filename, signal_function=self.qpicamera2.signal_done, name="raw")

        elif self.tabs.currentIndex() == 1:
            # shading procedure
            temperature_value = self.temperature_tab2.text()
            self.alsc_used.append(temperature_value)
            index = self.alsc_used.count(temperature_value)
            filename = folder_directory + "/alsc_" + temperature_value + "K_" + str(index) + ".dng"
            # update the lists
            listWidgetItem_shading = QListWidgetItem("alsc_" + temperature_value + "K_" + str(index) + ".dng")
            self.listWidget_shading.addItem(listWidgetItem_shading)
            self.button_tab2.setEnabled(False)
            cfg = picam2.create_still_configuration()
            picam2.switch_mode_and_capture_file(cfg, filename, signal_function=self.qpicamera2.signal_done, name="raw")

        else:
            # cac procedure
            self.cac_used += 1
            filename = folder_directory + "/cac_chart" + str(self.cac_used) + ".dng"
            self.button_tab3.setEnabled(False)
            cfg = picam2.create_still_configuration()
            picam2.switch_mode_and_capture_file(cfg, filename, signal_function=self.qpicamera2.signal_done, name="raw")

    # Continues the video and checks for macbeth plot in machbet
    def capture_done(self):
        global folder_directory, busy
        self.button_tab1.setText("Click to capture Photo")
        file_location = folder_directory + "/" + self.temperature_tab1.text() + "K_" + self.lux_tab1.text() + "L" + ".dng"
        if self.macbeth_bool is True and check(file_location, target) is False:
            self.macbeth_bool = False
            if self.macbeth_used.count(self.temperature_tab1.text()) > 1:
                self.list_of_files = list(dict.fromkeys(self.list_of_files))
                self.list_of_files.remove(self.temperature_tab1.text() + "K_" + self.lux_tab1.text() + "L" + ".dng")
            self.macbeth_used.remove(self.temperature_tab1.text())
            os.remove(file_location)
            # dialogue about bad macbeth
            if self.overlay_active:
                dialogue = QMessageBox()
                dialogue.setWindowTitle("ERROR")
                dialogue.setText("Image is too dark, please fix the lighting.")
                dialogue.exec()
            else:
                dialogue = QMessageBox()
                dialogue.setWindowTitle("ERROR")
                dialogue.setText("Could not find macbeth chart in the image, please take the photo again.")
                dialogue.exec()
        elif self.macbeth_bool:
            # update the lists
            self.list_of_files = list(dict.fromkeys(self.list_of_files))
            self.macbeth_bool = False
            # Print ut the list of files we have thta conatin macbeth
        self.listWidget_macbeth.clear()
        self.listWidget_macbeth.addItems(self.list_of_files)
        busy = False

    # Finishes ctt and closes app
    def on_button1_clicked(self):
        self.button_tab1_1.setText("Running ctt")
        self.button_tab2_1.setText("Running ctt")
        self.button_tab3_1.setText("Running ctt")
        self.button_tab1.setDisabled(True)
        self.button_tab2.setDisabled(True)
        self.button_tab3.setDisabled(True)
        QTimer.singleShot(200, self.capture_done_1)

    # final function that does ctt
    def capture_done_1(self):
        global folder_directory
        # does ctt with files and is done, but only if the folder has at east one macbeth and other one
        # firt pisp pi5 file
        from ctt_pisp import grid_size, json_template
        t = "pisp"
        json_output_pi5 = os.path.join(folder_directory, 'calibration_file_pi5.json')
        run_ctt(json_output_pi5, folder_directory, None, None, json_template, grid_size, t)
        # second vc4 pi4 file
        from ctt_vc4 import grid_size, json_template
        t = "vc4"
        json_output_pi4 = os.path.join(folder_directory, 'calibration_file_pi4.json')
        run_ctt(json_output_pi4, folder_directory, None, None, json_template, grid_size, t)

        # says that it has finished and written files in the folder_directory
        dialogue_done = QMessageBox()
        dialogue_done.setWindowTitle("DONE")
        dialogue_done.setText("Ctt is finished, and tunning files are written in folder at " + folder_directory)
        dialogue_done.exec()

        # stop everything
        picam2.stop()
        app.exit()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    execution_1 = FirstWindow()
    app.exec_()
    if start is True:
        execution_2 = App()
        sys.exit(app.exec_())
        picam2.stop()
