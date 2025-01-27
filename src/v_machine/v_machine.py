import cProfile
import glob
import gzip
import io
import os
import pickle
import pstats
import queue
import random
import sys
import time
from functools import lru_cache
from multiprocessing import Process, Queue, freeze_support
import numpy as np
import sounddevice as sd
from PIL import Image, ImageEnhance
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QComboBox, QMessageBox
from PyQt5.Qt import Qt, QPoint, pyqtSignal
from PyQt5 import QtGui
from PyQt5.QtGui import QGuiApplication
from type import mtd_video

sys.modules["mtd_video"] = mtd_video


def get_key_frame_array(key_frame_buffer):
    return np.asarray(Image.open(key_frame_buffer), dtype=np.uint8)


def load_video(path, q=None) -> mtd_video.MTDVideo:
    fp = gzip.open(path, "rb")
    print(f"loading file {path}")
    mtd = pickle.load(fp)
    print(f"finish loading file")
    fp.close()
    if q is not None:
        q.put(mtd)
    return mtd


def pil_to_pixmap(im):
    data = im.tobytes("raw", "RGB")
    qim = QtGui.QImage(data, im.size[0], im.size[1], QtGui.QImage.Format.Format_RGB888)
    pixmap = QtGui.QPixmap.fromImage(qim)
    return pixmap


class GUI(QWidget):

    signal = pyqtSignal(int, int)

    def __init__(
        self,
        video_dir,
        enable_profile=False,
        max_fps=30,
    ):
        super().__init__()
        self.loading_stage = 0
        self.video_dir = video_dir
        self.all_video_paths = sorted(glob.glob(f"{video_dir}/*.mtd"))
        if len(self.all_video_paths) == 0:
            print(f"No MTD videos at {video_dir}")
            return
        self.current_vid_idx = 0
        print(f"loading video from {video_dir}")
        mtd = load_video(self.all_video_paths[self.current_vid_idx])
        print("finish loading")
        self.mtd = None
        self.current_img_idx = None
        self.current_frame = None
        self.mtd_shape = None
        self.setStyleSheet("background-color: black;")
        self.setWindowTitle("Visual Loop Machine by Li Yang Ku")
        self.setup_mtd_video(mtd)

        self.image_size = (512, 512)
        self.resize(550, 630)

        self.canvas = QLabel(self)
        self.canvas.resize(*self.image_size)
        self.default_img_loc = [19, 19]
        self.canvas.move(*self.default_img_loc)

        self.instruction_loc = [50, 545]
        self.instruction = QLabel(self)

        self.instruction.setText(
            "→ next video, ← last video, ↑ increase change, ↓ decrease change\nSpace: toggle fullscreen, Esc: exit fullscreen\n\nInput source:"
        )
        self.instruction.setStyleSheet("color: white;")
        self.instruction.move(*self.instruction_loc)
        self.instruction.show()

        self.combobox_loc = [150, 590]
        self.combobox = QComboBox(self)
        self.combobox.move(*self.combobox_loc)
        self.combobox.resize(350, 30)
        self.combobox.setStyleSheet(
            "color: #75F5CA; selection-color: white; selection-background-color: #4DCDA2"
        )
        self.combobox.show()

        orig_img = Image.fromarray(self.current_frame)
        img = orig_img.resize(self.image_size, Image.HAMMING)

        self.canvas.setPixmap(pil_to_pixmap(img))

        self.fullscreen_state = False
        self.dim_1_dir = 0
        self.dim_0_dir = 0
        self.threshold = 0.9
        self.enable_profile = enable_profile
        if enable_profile:
            self.pr = cProfile.Profile()
        self.load_next = False
        self.load_previous = False
        self.brightness = 1
        self.pause = False
        self.max_fps = max_fps
        self.previous_canvas_size = None

        self.sound_monitor = None
        self.cidx_mapping = {}
        self._update_count = 0
        self._now = None

    def set_sound_monitor(self, sound_monitor):
        self.sound_monitor = sound_monitor
        self.cidx_mapping = {}
        for i, device in enumerate(self.sound_monitor.devices):
            if device["max_input_channels"] > 0:
                self.cidx_mapping[self.combobox.count()] = i
                self.combobox.addItem(device["name"])

        default_idx = self.combobox.count() - 1
        self.combobox.setCurrentIndex(default_idx)
        self.sound_monitor.current_device_id = self.cidx_mapping[default_idx]
        self.combobox.currentIndexChanged.connect(self.select_sound_device)
        self.combobox.keyPressEvent = self.keyPressEvent

    def select_sound_device(self, index):
        cindex = self.combobox.currentIndex()
        device_id = self.cidx_mapping[cindex]
        if device_id != self.sound_monitor.current_device_id:
            print(f"switch to device {self.sound_monitor.devices[device_id]['name']}")
            self.sound_monitor.close()
            self.sound_monitor.run(device_id)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == Qt.Key_Space:
            self.toggle_fullscreen()
        elif event.key() == Qt.Key_Right:
            self.right()
        elif event.key() == Qt.Key_Left:
            self.left()
        elif event.key() == Qt.Key_Up:
            self.up()
        elif event.key() == Qt.Key_Down:
            self.down()
        elif event.key() == Qt.Key_Escape:
            self.end_fullscreen()

    def clear_mtd_memory(self):
        if hasattr(self, "mtd"):
            del self.mtd

        if hasattr(self, "key_frames"):
            del self.key_frames

    def setup_mtd_video(self, mtd):
        self.mtd = mtd
        first_idx = list(self.mtd.key_frames.keys())[0]
        self.current_img_idx = list(first_idx)
        self.current_frame = get_key_frame_array(self.mtd.key_frames[first_idx])
        self.mtd_shape = (mtd.diff_array_shape[0], mtd.diff_array_shape[1])

    @lru_cache(maxsize=500)
    def get_key_frame(self, mtd, img_idx):

        if img_idx in mtd.key_frames:
            return get_key_frame_array(mtd.key_frames[img_idx])
        return None

    @lru_cache(maxsize=500)
    def get_diff_image(self, mtd, dim0, dim1, dir):
        if mtd.diff_array[dim0][dim1][dir] is None:
            print(f"no diff for {dim0}, {dim1}, {dir}")
            return None
        diff_image = (
            np.asarray(Image.open(mtd.diff_array[dim0][dim1][dir]), dtype=np.int16)
            - 128
        ) * 2
        return diff_image

    def down(self):
        self.threshold += 0.1
        self.threshold = min(1.1, self.threshold)
        print(f"threshold {self.threshold}")

    def up(self):
        self.threshold -= 0.1
        self.threshold = max(0.5, self.threshold)
        print(f"threshold {self.threshold}")

    def right(self):
        if not self.load_previous:
            self.load_next = True

    def left(self):
        if not self.load_next:
            self.load_previous = True

    def toggle_fullscreen(self):
        if self.fullscreen_state is True:
            self.end_fullscreen()
        else:
            self.start_full_screen()

    def start_full_screen(self):
        self.instruction.hide()
        self.combobox.hide()
        self.fullscreen_state = True
        self.showFullScreen()

        screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(0, 0)))

        full_width = screen.size().width()
        full_height = screen.size().height()
        size = min([full_width, full_height])
        self.image_size = (size, size)
        self.canvas.resize(*self.image_size)
        self.canvas.move(int((full_width - size) / 2), int((full_height - size) / 2))
        return

    def end_fullscreen(self):
        self.image_size = (512, 512)
        self.fullscreen_state = False
        self.showNormal()
        self.canvas.resize(*self.image_size)

        self.canvas.move(*self.default_img_loc)
        self.instruction.show()
        self.combobox.show()

        if self.enable_profile:
            s = io.StringIO()
            ps = pstats.Stats(self.pr, stream=s).sort_stats("cumulative")
            ps.print_stats()
            print(s.getvalue())
        return

    def load_next_video(self, previous=False):
        if self.loading_stage == 0:
            self.loading_stage = 1

            if previous:
                self.current_vid_idx -= 1
            else:
                self.current_vid_idx += 1
            self.current_vid_idx = self.current_vid_idx % len(self.all_video_paths)
            print("loading video")
            self.q = Queue()
            p = Process(
                target=load_video,
                args=(self.all_video_paths[self.current_vid_idx], self.q),
            )
            p.start()
        elif self.loading_stage == 1:
            try:
                self.brightness = max(self.brightness - 0.02, 0)
                if self.brightness == 0:
                    mtd = self.q.get(block=False)
                    self.setup_mtd_video(mtd)
                    self.loading_stage = 2
            except queue.Empty:
                time.sleep(0.1)
                pass
        elif self.loading_stage == 2:
            self.brightness = min(self.brightness + 0.02, 1)
            if self.brightness == 1:
                self.load_next = False
                self.load_previous = False
                self.loading_stage = 0

    def update(self, dim_1_dir=0, dim_0_dir=0):
        if self.load_next:
            self.load_next_video()
        elif self.load_previous:
            self.load_next_video(previous=True)
        if self.pause:
            return False
        if self.fullscreen_state and self.enable_profile:
            self.pr.enable()

        # First check if target img idx is key frame
        target_img_idx = self.current_img_idx.copy()

        if dim_1_dir == 1:
            if target_img_idx[1] == self.mtd_shape[1] - 1:
                dim_0_dir = 1
            else:
                target_img_idx[1] += 1
        elif dim_1_dir == -1:
            if target_img_idx[1] == 0:
                dim_0_dir = 1
            else:
                target_img_idx[1] -= 1

        if dim_0_dir == 1:
            target_img_idx[0] = (target_img_idx[0] + 1) % self.mtd_shape[0]

        keyframe = self.get_key_frame(self.mtd, tuple(target_img_idx))

        if keyframe is not None:
            self.current_frame = keyframe
        # Use difference to generate current frame
        else:
            # first move in dimension 1
            if dim_1_dir == 1:
                if not self.current_img_idx[1] == self.mtd_shape[1] - 1:
                    next_img_idx = self.current_img_idx.copy()
                    next_img_idx[1] += 1
                    keyframe = self.get_key_frame(self.mtd, tuple(next_img_idx))
                    if keyframe is not None:
                        self.current_frame = keyframe
                    else:
                        diff_image = self.get_diff_image(
                            mtd=self.mtd,
                            dim0=self.current_img_idx[0],
                            dim1=self.current_img_idx[1],
                            dir=1,
                        )
                        self.current_frame = (
                            np.clip(
                                self.current_frame.astype(np.int16) + diff_image,
                                0,
                                255,
                            )
                        ).astype(np.uint8)
                    self.current_img_idx = next_img_idx

            elif dim_1_dir == -1:
                if not self.current_img_idx[1] == 0:
                    next_img_idx = self.current_img_idx.copy()
                    next_img_idx[1] -= 1
                    keyframe = self.get_key_frame(self.mtd, tuple(next_img_idx))
                    if keyframe is not None:
                        self.current_frame = keyframe
                    else:
                        diff_image = self.get_diff_image(
                            mtd=self.mtd,
                            dim0=self.current_img_idx[0],
                            dim1=self.current_img_idx[1] - 1,
                            dir=1,
                        )
                        self.current_frame = (
                            np.clip(
                                self.current_frame.astype(np.int16) - diff_image,
                                0,
                                255,
                            )
                        ).astype(np.uint8)
                    self.current_img_idx = next_img_idx
            if dim_0_dir == 1:
                # move in dimension 0
                diff_image = self.get_diff_image(
                    mtd=self.mtd,
                    dim0=self.current_img_idx[0],
                    dim1=self.current_img_idx[1],
                    dir=0,
                )
                self.current_frame = (
                    np.clip(self.current_frame.astype(np.int16) + diff_image, 0, 255)
                ).astype(np.uint8)

        self.current_img_idx = target_img_idx

        orig_img = Image.fromarray(self.current_frame)
        if self.brightness != 1:
            enhancer = ImageEnhance.Brightness(orig_img)
            orig_img = enhancer.enhance(self.brightness)

        img = orig_img.resize(self.image_size, Image.NEAREST)

        self.canvas.setPixmap(pil_to_pixmap(img))

        self._update_count += 1

        if self._update_count % 100 == 0:
            if self._now is not None:
                elapsed = time.time() - self._now
                print(f"frame per second {100 / elapsed}")
            self._now = time.time()
            self._update_count = 0

        if self.fullscreen_state and self.enable_profile:
            self.pr.disable()


class SoundMonitor:
    def __init__(self, gui: GUI, max_fps: int):
        self.gui = gui
        self.signal = gui.signal
        self.last_n = [0]
        self.now = None
        self.devices = sd.query_devices()
        self.current_device_id = None
        self.max_fps = max_fps

    def callback(self, indata, frames, ctime, status):
        amplitude = np.mean(abs(indata))
        n_average = np.mean(self.last_n)
        dim_0_dir = 0
        if amplitude < (n_average * (self.gui.threshold - 0.01)) or amplitude < 0.001:
            dim_1_dir = 1
        elif amplitude > n_average * (self.gui.threshold + 0.01):
            dim_1_dir = -1
        else:
            dim_1_dir = 0

        if 10 * amplitude > random.random() * self.gui.threshold:
            dim_0_dir = 1

        self.signal.emit(dim_1_dir, dim_0_dir)
        self.last_n.append(amplitude)
        self.last_n = self.last_n[-200:]

    def run(self, device_id):
        self.current_device_id = device_id
        device = self.devices[self.current_device_id]
        samplerate = device["default_samplerate"]
        channels = device["max_input_channels"]
        blocksize = int(samplerate // self.max_fps)
        self.stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            callback=self.callback,
            blocksize=blocksize,
            device=device["name"],
            latency="low"
        )
        self.stream.start()

    def close(self):
        self.stream.close()


def get_video_directroy(file_dir: str):
    default_video_dir = os.path.join(file_dir, "../../mtd_videos")
    if os.path.isdir(default_video_dir):
        return default_video_dir
    else:
        home_dir = os.path.expanduser("~")
        if sys.platform == "linux" or sys.platform == "linux2":
            video_dir = os.path.join(home_dir, "Videos/mtd_videos/")
        elif sys.platform == "darwin":
            video_dir = os.path.join(home_dir, "Movies/mtd_videos/")
        elif sys.platform == "win32":
            video_dir = os.path.join(home_dir, "Videos/mtd_videos/")

    if not os.path.isdir(video_dir):
        os.makedirs(video_dir)

    num_videos = len(glob.glob(f"{video_dir}/*.mtd"))
    if num_videos == 0:
        msg = QMessageBox()
        msg.setText(
            f"No MTD videos in directory {video_dir}. MTD Videos can be downloaded from "
            f'<a href="https://drive.google.com/drive/folders/16wlG6fFPS-srPqVNeYKTvZyl0b4hTfPi?usp=sharing">'
            f" here.</a> Loading example videos instead."
        )
        msg.exec_()
        video_dir = os.path.join(sys._MEIPASS, "mtd_videos")

    return video_dir


def get_icon_directory(file_dir: str):
    default_icon_dir = os.path.join(file_dir, "../../v_machine_icon.gif")
    if os.path.exists(default_icon_dir):
        return default_icon_dir
    icon_dir = os.path.join(sys._MEIPASS, "files/v_machine_icon.gif")
    return icon_dir


if __name__ == "__main__":
    freeze_support()
    max_fps = 30
    file_dir = os.path.dirname(__file__)
    app = QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(get_icon_directory(file_dir)))
    video_dir = get_video_directroy(file_dir)
    gui = GUI(video_dir=video_dir, max_fps=max_fps)
    sm = SoundMonitor(gui, max_fps=max_fps)
    sm.signal.connect(gui.update)
    gui.set_sound_monitor(sm)
    sm.run(sm.current_device_id)
    gui.show()
    app.exec_()
    sm.close()
