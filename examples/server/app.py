import sys
import os
import io
# Add the parent directory to path to allow importing pidog if running from examples/server
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import time
import threading
import logging

# Try to import Pidog and Vilib with debug info
try:
    from pidog import Pidog
except ImportError:
    print("Error: Could not import 'pidog'. Make sure it is installed or you are running from the correct directory.")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

try:
    from vilib import Vilib
except ImportError:
    print("WARNING: Could not import 'vilib'. Video feed will not work.")
    print("Please ensure vilib is installed correctly (check the 'picamera2' branch).")
    print(f"Current sys.path: {sys.path}")
    
    # Mock Vilib to allow server to start without video
    class MockVilib:
        @staticmethod
        def camera_start(vflip=False, hflip=False):
            print("MockVilib: camera_start")
        @staticmethod
        def display(local=False, web=True):
            print("MockVilib: display")
        @staticmethod
        def face_detect_switch(enable):
            print(f"MockVilib: face_detect_switch {enable}")
        @staticmethod
        def camera_close():
            print("MockVilib: camera_close")
            
    Vilib = MockVilib

app = Flask(__name__)
CORS(app)



import subprocess
from pidog import preset_actions

# Camera class for Pi Zero W (using picamera)
class PiCameraStream:
    def __init__(self):
        self.camera = None
        try:
            import picamera
            self.camera = picamera.PiCamera()
            self.camera.resolution = (320, 240)
            self.camera.framerate = 24
            time.sleep(2.0)
        except Exception as e:
            raise ImportError(f"Picamera failed: {e}")

    def get_frame(self):
        if not self.camera:
            return None
        stream = io.BytesIO()
        self.camera.capture(stream, 'jpeg', use_video_port=True)
        stream.seek(0)
        return stream.read()

# Camera class for newer OS (using libcamera-vid / rpicam-vid)
class LibCameraStream:
    def __init__(self):
        self.process = None
        # Try rpicam-vid (Bookworm) then libcamera-vid (Bullseye)
        # Use --timeout 0 for infinite stream
        # Use --inline to ensure headers are sent with every frame (critical for parsing)
        # Use --nopreview to save resources
        commands = [
            ['rpicam-vid', '-t', '0', '--codec', 'mjpeg', '--width', '320', '--height', '240', '--framerate', '15', '--inline', '--nopreview', '-o', '-'],
            ['libcamera-vid', '-t', '0', '--codec', 'mjpeg', '--width', '320', '--height', '240', '--framerate', '15', '--inline', '--nopreview', '-o', '-']
        ]
        
        for cmd in commands:
            try:
                self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                time.sleep(1.0) # Wait for camera to start
                if self.process.poll() is None:
                    print(f"Started camera with: {' '.join(cmd)}")
                    break
                else:
                    self.process = None
            except FileNotFoundError:
                continue
                
        if not self.process:
            raise ImportError("Could not start rpicam-vid or libcamera-vid")

    def get_frame(self):
        return None

# Wrapper to handle the background reading for LibCamera
class CameraWrapper:
    def __init__(self):
        self.stream = None
        self.latest_frame = None
        self.running = True
        self.thread = None
        
        # Try PiCamera first
        try:
            self.stream = PiCameraStream()
            print("Using PiCamera")
            return 
        except ImportError:
            pass
            
        # Try LibCamera
        try:
            self.lib_stream = LibCameraStream()
            print("Using LibCamera")
            self.thread = threading.Thread(target=self._update_libcamera)
            self.thread.daemon = True
            self.thread.start()
        except ImportError:
            print("WARNING: No camera found (PiCamera or LibCamera). Video disabled.")

    def _update_libcamera(self):
        # Read from stdout and parse JPEGs
        stream = self.lib_stream.process.stdout
        bytes_buffer = b''
        while self.running and self.lib_stream.process:
            try:
                # Read in larger chunks
                chunk = stream.read(4096)
                if not chunk:
                    break
                bytes_buffer += chunk
                
                # Find start and end of JPEG
                a = bytes_buffer.find(b'\xff\xd8')
                b = bytes_buffer.find(b'\xff\xd9')
                
                if a != -1 and b != -1:
                    if a < b:
                        jpg = bytes_buffer[a:b+2]
                        self.latest_frame = jpg
                        bytes_buffer = bytes_buffer[b+2:]
                    else:
                        # End before start, discard garbage
                        bytes_buffer = bytes_buffer[a:]
            except Exception as e:
                print(f"Error reading stream: {e}")
                time.sleep(0.1)

    def get_frame(self):
        if hasattr(self, 'stream') and self.stream:
            return self.stream.get_frame()
        return self.latest_frame

# Global camera instance
camera_wrapper = None

def generate_frames():
    global camera_wrapper
    if camera_wrapper is None:
        camera_wrapper = CameraWrapper()
        
    while True:
        frame = camera_wrapper.get_frame()
        if frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            time.sleep(0.05) 
        else:
            time.sleep(0.1)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/mjpg') # Alias for Vilib compatibility
def mjpg():
    return video_feed()


# Initialize Pidog
try:
    my_dog = Pidog()
    # Set default head position
    my_dog.head_move([[0, 0, 0]], immediately=True, speed=80)
except Exception as e:
    logging.error(f"Failed to initialize Pidog: {e}")
    my_dog = None

# Initialize Vilib for camera
try:
    Vilib.camera_start(vflip=False, hflip=False)
    Vilib.display(local=False, web=True) # Starts web streaming on port 9000 by default
    Vilib.face_detect_switch(False) # Disable face detection to save resources
except Exception as e:
    logging.error(f"Failed to initialize Vilib: {e}")

from pidog.preset_actions import pant, body_twisting, bark, shake_head, push_up, howling, bark_action
from pidog.walk import Walk
from math import sin

class ExampleRunner:
    def __init__(self):
        self.running = False
        self.thread = None
        self.current_example = None

    def start(self, example_name):
        if self.running:
            self.stop()
        
        self.running = True
        self.current_example = example_name
        
        target = None
        if 'wake_up' in example_name:
            target = self.run_wake_up
        elif 'patrol' in example_name:
            target = self.run_patrol
        elif 'rest' in example_name:
            target = self.run_rest
        elif 'pushup' in example_name:
            target = self.run_pushup
        elif 'howling' in example_name:
            target = self.run_howling
        elif 'balance' in example_name:
            target = self.run_balance
        elif 'response' in example_name:
            target = self.run_response
        elif 'ball_track' in example_name:
            target = self.run_ball_track
        elif 'super_dog' in example_name:
            target = self.run_super_dog
        elif 'kids_play' in example_name:
            target = self.run_kids_play
        
        if target:
            self.thread = threading.Thread(target=target)
            self.thread.start()
            return True, f"Started {example_name}"
        else:
            self.running = False
            return False, "Example not supported for internal execution yet"

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self.thread = None
        self.current_example = None
        # Reset robot state
        if my_dog:
            my_dog.body_stop()
            my_dog.head_move([[0, 0, 0]], immediately=True, speed=80)
            # Reset Vilib if used
            try:
                Vilib.camera_close()
            except:
                pass

    def sleep(self, duration):
        """Interruptible sleep"""
        end_time = time.time() + duration
        while self.running and time.time() < end_time:
            time.sleep(0.05)

    def run_wake_up(self):
        try:
            print("Running wake_up")
            if not my_dog: return
            my_dog.head_move([[0, 0, -30]], immediately=True, speed=80)
            self.sleep(1)
            if not self.running: return
            my_dog.rgb_strip.set_mode('listen', color='yellow', bps=0.6, brightness=0.8)
            my_dog.do_action('stretch', speed=50)
            my_dog.head_move([[0, 0, 30]]*2, immediately=True)
            my_dog.wait_all_done()
            self.sleep(0.2)
            if not self.running: return
            body_twisting(my_dog)
            my_dog.wait_all_done()
            self.sleep(0.5)
            if not self.running: return
            my_dog.head_move([[0, 0, -30]], immediately=True, speed=90)
            my_dog.do_action('sit', speed=25)
            my_dog.wait_legs_done()
            my_dog.do_action('wag_tail', step_count=10, speed=100)
            my_dog.rgb_strip.set_mode('breath', color=[245, 10, 10], bps=2.5, brightness=0.8)
            pant(my_dog, pitch_comp=-30, volume=80)
            my_dog.wait_all_done()
            if not self.running: return
            my_dog.do_action('wag_tail', step_count=10, speed=30)
            my_dog.rgb_strip.set_mode('breath', 'pink', bps=0.5)
            while self.running:
                self.sleep(1)
        except Exception as e:
            print(f"Error in wake_up: {e}")
            import traceback
            traceback.print_exc()

    def run_patrol(self):
        try:
            print("Running patrol")
            if not my_dog: return
            DANGER_DISTANCE = 15
            my_dog.do_action('stand', speed=80)
            my_dog.wait_all_done()
            self.sleep(0.5)
            stand = my_dog.legs_angle_calculation([[0, 80], [0, 80], [30, 75], [30, 75]])
            while self.running:
                distance = round(my_dog.read_distance(), 2)
                if distance > 0 and distance < DANGER_DISTANCE:
                    print(f"DANGER! Distance: {distance}")
                    my_dog.body_stop()
                    head_yaw = my_dog.head_current_angles[0]
                    my_dog.rgb_strip.set_mode('bark', 'red', bps=2)
                    my_dog.tail_move([[0]], speed=80)
                    my_dog.legs_move([stand], speed=70)
                    my_dog.wait_all_done()
                    self.sleep(0.5)
                    bark(my_dog, [head_yaw, 0, 0])
                    while self.running and distance < DANGER_DISTANCE:
                        distance = round(my_dog.read_distance(), 2)
                        self.sleep(0.01)
                else:
                    my_dog.rgb_strip.set_mode('breath', 'white', bps=0.5)
                    my_dog.do_action('forward', step_count=2, speed=98)
                    my_dog.do_action('shake_head', step_count=1, speed=80)
                    my_dog.do_action('wag_tail', step_count=5, speed=99)
                self.sleep(0.01)
        except Exception as e:
            print(f"Error in patrol: {e}")
            import traceback
            traceback.print_exc()

    def run_rest(self):
        try:
            print("Running rest")
            if not my_dog: return
            def loop_around(amplitude=60, interval=0.5, speed=100):
                if not self.running: return
                my_dog.head_move([[amplitude,0,0]], immediately=True, speed=speed)
                my_dog.wait_all_done()
                self.sleep(interval)
                if not self.running: return
                my_dog.head_move([[-amplitude,0,0]], immediately=True, speed=speed)
                my_dog.wait_all_done()
                self.sleep(interval)
                if not self.running: return
                my_dog.head_move([[0,0,0]], immediately=True, speed=speed)
                my_dog.wait_all_done()
            def is_sound():
                if my_dog.ears.isdetected():
                    direction = my_dog.ears.read()
                    if direction != 0: return True
                return False
            my_dog.wait_all_done()
            my_dog.do_action('lie', speed=50)
            my_dog.wait_all_done()
            while self.running:
                my_dog.rgb_strip.set_mode('breath', 'pink', bps=0.3)
                my_dog.head_move([[0,0,-40]], immediately=True, speed=5)
                my_dog.do_action('doze_off', speed=92)
                self.sleep(1)
                if is_sound(): pass 
                while self.running and is_sound() is False:
                    my_dog.do_action('doze_off', speed=92)
                    self.sleep(0.2)
                if not self.running: break
                my_dog.rgb_strip.set_mode('boom', 'yellow', bps=1)
                my_dog.body_stop()
                self.sleep(0.1)
                my_dog.do_action('stand', speed=80)
                my_dog.head_move([[0, 0, 0]], immediately=True, speed=80)
                my_dog.wait_all_done()
                loop_around(60, 1, 60)
                self.sleep(0.5)
                my_dog.speak('confused_3', volume=80)
                my_dog.do_action('tilting_head_left', speed=80)
                my_dog.wait_all_done()
                self.sleep(1.2)
                my_dog.head_move([[0, 0, -10]], immediately=True, speed=80)
                my_dog.wait_all_done()
                self.sleep(0.4)
                shake_head(my_dog)
                self.sleep(0.2)
                my_dog.rgb_strip.set_mode('breath', 'pink', bps=1)
                my_dog.do_action('lie', speed=50)
                my_dog.wait_all_done()
                self.sleep(1)
        except Exception as e:
            print(f"Error in rest: {e}")
            import traceback
            traceback.print_exc()

    def run_pushup(self):
        try:
            print("Running pushup")
            if not my_dog: return
            my_dog.legs_move([[45, -25, -45, 25, 80, 70, -80, -70]], speed=50)
            my_dog.head_move([[0, 0, -20]], speed=90)
            my_dog.wait_all_done()
            self.sleep(0.5)
            bark(my_dog, [0, 0, -20])
            self.sleep(0.1)
            bark(my_dog, [0, 0, -20])
            self.sleep(1)
            my_dog.rgb_strip.set_mode("speak", color="blue", bps=2)
            while self.running:
                push_up(my_dog, speed=92)
                bark(my_dog, [0, 0, -40])
                self.sleep(0.4)
        except Exception as e:
            print(f"Error in pushup: {e}")
            import traceback
            traceback.print_exc()

    def run_howling(self):
        try:
            print("Running howling")
            if not my_dog: return
            my_dog.do_action('sit', speed=50)
            my_dog.head_move([[0, 0, 0]], pitch_comp=-40, immediately=True, speed=80)
            self.sleep(0.5)
            while self.running:
                howling(my_dog)
        except Exception as e:
            print(f"Error in howling: {e}")
            import traceback
            traceback.print_exc()

    def run_balance(self):
        try:
            print("Running balance")
            if not my_dog: return
            # Demo of balance: stand then cycle poses
            stand_coords = [[[-15, 95], [-15, 95], [5, 90], [5, 90]]]
            forward_coords = Walk(fb=Walk.FORWARD, lr=Walk.STRAIGHT).get_coords()
            backward_coords = Walk(fb=Walk.BACKWARD, lr=Walk.STRAIGHT).get_coords()
            
            current_coords = stand_coords
            current_pose = {'x': 0, 'y': 0, 'z': 80}
            current_rpy = {'roll': 0, 'pitch': 0, 'yaw': 0}
            
            my_dog.do_action('stand', speed=80)
            my_dog.wait_legs_done()
            
            # Simple demo loop
            modes = [stand_coords, forward_coords, backward_coords]
            idx = 0
            
            while self.running:
                # Cycle through modes every few seconds for demo
                # Or just hold stand if that's what balance means (it adjusts to terrain?)
                # The original script uses IMU in move_thread implicitly? 
                # No, the original script just sets legs to coords. 
                # Wait, 10_balance.py sets pid=True in set_rpy, so it uses IMU for self-balancing!
                
                # We need a loop that updates servos based on IMU
                for coord in current_coords:
                    if not self.running: break
                    my_dog.set_rpy(**current_rpy, pid=True)
                    my_dog.set_pose(**current_pose)
                    my_dog.set_legs(coord)
                    angles = my_dog.pose2legs_angle()
                    my_dog.legs.servo_move(angles, speed=98)
                    # self.sleep(0.01) # Small delay for loop? Original has no delay in thread
                
                # Switch modes for demo effect? Or just keep balancing?
                # Let's just keep balancing in stand mode for now
                pass
                
        except Exception as e:
            print(f"Error in balance: {e}")
            import traceback
            traceback.print_exc()

    def run_response(self):
        try:
            print("Running response")
            if not my_dog: return
            
            def lean_forward():
                my_dog.speak('angry', volume=80)
                bark_action(my_dog)
                self.sleep(0.2)
                bark_action(my_dog)
                self.sleep(0.4)
                bark_action(my_dog)

            def head_nod(step):
                y = 0; r = 0; p = 30
                angs = []
                for i in range(20):
                    r = round(10*sin(i*0.314), 2)
                    p = round(20*sin(i*0.314) + 10, 2)
                    angs.append([y, r, p])
                my_dog.head_move(angs*step, immediately=False, speed=80)

            my_dog.do_action('stand', step_count=1, speed=70)
            my_dog.rgb_strip.set_mode('breath', color='pink', bps=1, brightness=0.8)
            
            while self.running:
                # alert
                dist = my_dog.read_distance()
                if dist < 15 and dist > 1:
                    my_dog.head_move([[0, 0, 0]], immediately=True, speed=90)
                    my_dog.tail_move([[0]], immediately=True, speed=80)
                    my_dog.rgb_strip.set_mode('bark', color='red', bps=2, brightness=0.8)
                    my_dog.do_action('backward', step_count=1, speed=95)
                    my_dog.wait_all_done()
                    lean_forward()
                    while len(my_dog.legs_action_buffer) > 0:
                        self.sleep(0.1)
                    my_dog.do_action('stand', step_count=1, speed=90)
                    self.sleep(0.5)
                # relax
                elif my_dog.dual_touch.read() != 'N':
                    if len(my_dog.head_action_buffer) < 2:
                        head_nod(1)
                        my_dog.do_action('wag_tail', step_count=10, speed=80)
                        my_dog.rgb_strip.set_mode('listen', color="#8A2BE2", bps=0.35, brightness=0.8)
                # calm
                else:
                    my_dog.rgb_strip.set_mode('breath', color='pink', bps=1, brightness=0.8)
                    my_dog.tail_stop()
                self.sleep(0.2)
        except Exception as e:
            print(f"Error in response: {e}")
            import traceback
            traceback.print_exc()

    def run_ball_track(self):
        try:
            print("Running ball_track")
            if not my_dog: return
            
            Vilib.camera_start(vflip=False, hflip=False)
            Vilib.display(local=False, web=True)
            Vilib.color_detect(color="red")
            self.sleep(0.2)
            
            yaw = 0
            pitch = 0
            STEP = 0.5
            
            my_dog.do_action('stand', speed=50)
            my_dog.head_move([[yaw, 0, pitch]], immediately=True, speed=80)
            my_dog.wait_legs_done()
            my_dog.wait_head_done()
            self.sleep(0.5)
            
            while self.running:
                ball_x = Vilib.detect_obj_parameter['color_x'] - 320
                ball_y = Vilib.detect_obj_parameter['color_y'] - 240
                width = Vilib.detect_obj_parameter['color_w']
                
                if ball_x > 15 and yaw > -80:
                    yaw -= STEP
                elif ball_x < -15 and yaw < 80:
                    yaw += STEP
                    
                if ball_y > 25:
                    pitch -= STEP
                    if pitch < -40: pitch = -40
                elif ball_y < -25:
                    pitch += STEP
                    if pitch > 20: pitch = 20
                    
                my_dog.head_move([[yaw, 0, pitch]], immediately=True, speed=100)
                
                if width == 0:
                    pitch = 0
                    yaw = 0
                elif width < 300:
                    if my_dog.is_legs_done():
                        if yaw < -30:
                            my_dog.do_action('turn_right', speed=98)
                        elif yaw > 30:
                            my_dog.do_action('turn_left', speed=98)
                        else:
                            my_dog.do_action('forward', speed=98)
                self.sleep(0.02)
                
        except Exception as e:
            print(f"Error in ball_track: {e}")
            import traceback
            traceback.print_exc()
        finally:
            Vilib.camera_close()

    def run_super_dog(self):
        try:
            print("Running super_dog")
            if not my_dog: return
            
            # 1. Intro: Wake up and Greet
            print("Phase 1: Wake up")
            my_dog.head_move([[0, 0, -30]], immediately=True, speed=80)
            self.sleep(0.5)
            my_dog.do_action('stand', speed=80)
            my_dog.wait_all_done()
            
            # RGB Rainbow effect (simulated by cycling colors)
            # Use valid colors from rgb_strip.py or RGB lists
            colors = ['red', [255, 128, 0], 'yellow', 'green', 'blue', 'magenta'] # Orange as list, purple->magenta
            for color in colors:
                if not self.running: return
                my_dog.rgb_strip.set_mode('breath', color=color, bps=5, brightness=0.8)
                self.sleep(0.5)
            
            # 2. Excitement
            print("Phase 2: Excitement")
            if not self.running: return
            my_dog.do_action('wag_tail', step_count=20, speed=100)
            bark(my_dog, [0, 0, 0])
            self.sleep(0.5)
            pant(my_dog)
            self.sleep(1)

            # 3. Exercise (Pushups)
            print("Phase 3: Exercise")
            if not self.running: return
            my_dog.rgb_strip.set_mode('speak', color='blue', bps=2)
            for _ in range(3):
                if not self.running: break
                push_up(my_dog, speed=95)
                self.sleep(0.5)
            my_dog.do_action('stand', speed=80)
            my_dog.wait_all_done()

            # 4. Show off (Howl)
            print("Phase 4: Howl")
            if not self.running: return
            my_dog.do_action('sit', speed=60)
            my_dog.wait_all_done()
            howling(my_dog)
            self.sleep(1)

            # 5. Patrol (Trot forward)
            print("Phase 5: Patrol")
            if not self.running: return
            my_dog.do_action('stand', speed=80)
            my_dog.wait_all_done()
            my_dog.rgb_strip.set_mode('breath', 'green', bps=1)
            
            # Trot forward for a bit
            my_dog.do_action('forward', step_count=5, speed=98)
            my_dog.wait_legs_done()
            
            # Scan
            my_dog.head_move([[45, 0, 0]], speed=80)
            my_dog.wait_head_done()
            self.sleep(0.5)
            my_dog.head_move([[-45, 0, 0]], speed=80)
            my_dog.wait_head_done()
            self.sleep(0.5)
            my_dog.head_move([[0, 0, 0]], speed=80)
            my_dog.wait_head_done()

            # 6. Relax
            print("Phase 6: Relax")
            if not self.running: return
            my_dog.do_action('sit', speed=50)
            my_dog.wait_legs_done()
            self.sleep(0.5)
            
            # Yawn/Stretch head
            my_dog.head_move([[0, 0, 30]], speed=60)
            self.sleep(1)
            my_dog.head_move([[0, 0, 0]], speed=60)
            
            # Lie down
            my_dog.do_action('lie', speed=40)
            my_dog.wait_all_done()
            my_dog.rgb_strip.set_mode('breath', 'pink', bps=0.2)
            
            # Sleep loop
            while self.running:
                my_dog.do_action('doze_off', speed=80)
                self.sleep(1)

        except Exception as e:
            print(f"Error in super_dog: {e}")
            import traceback
            traceback.print_exc()

    def run_kids_play(self):
        try:
            print("Running kids_play")
            if not my_dog: return
            
            # Constants - Tuned for stability
            # Gravity is approx -16384
            # Require stronger acceleration to trigger
            ACC_LIFT_THRESH = -20000 # ~1.2G (Accelerating UP)
            ACC_LAND_THRESH = -10000 # ~0.6G (Weightless/Drop)
            DIST_THRESH = 10         # Reduced to 10cm to prevent false positives
            
            # State
            state = 'SLEEP' # SLEEP, AWAKE, SUPERMAN, INTERACTING
            state_timer = 0
            last_doze_time = 0
            last_sound_time = 0
            
            # Superman detection vars
            upflag = False
            downflag = False
            
            # Initial Setup
            my_dog.do_action('lie', speed=50)
            my_dog.wait_all_done()
            # Wait for robot to settle
            self.sleep(0.5) 
            my_dog.rgb_strip.set_mode('breath', 'pink', bps=0.3)
            
            while self.running:
                current_time = time.time()
                
                # --- SENSOR POLLING (Fast) ---
                ax = my_dog.accData[0]
                touch = my_dog.dual_touch.read()
                is_touched = touch != 'N'
                distance = my_dog.read_distance()
                
                sound_dir = 0
                if my_dog.ears.isdetected():
                    sound_dir = my_dog.ears.read()

                # --- SUPERMAN DETECTION (Priority 1) ---
                # Logic adapted from 6_be_picked_up.py
                # Always check this, even if interacting
                if ax < ACC_LIFT_THRESH:
                    if not upflag: upflag = True
                    if downflag:
                        # Landed
                        if state == 'SUPERMAN':
                            print("Landed!")
                            state = 'AWAKE'
                            my_dog.rgb_strip.set_mode('breath', 'cyan', bps=0.5)
                            my_dog.do_action('stand', speed=80)
                        downflag = False
                        
                if ax > ACC_LAND_THRESH:
                    if upflag:
                        # Flying
                        if state != 'SUPERMAN':
                            print("Flying!")
                            state = 'SUPERMAN'
                            my_dog.rgb_strip.set_mode('boom', 'red', bps=3)
                            my_dog.legs.servo_move([45, -45, 90, -80, 90, 90, -90, -90], speed=60)
                            my_dog.speak('woohoo', volume=80)
                            my_dog.do_action('wag_tail', step_count=10, speed=100)
                        upflag = False
                    if not downflag: downflag = True

                # If Superman, skip other logic
                if state == 'SUPERMAN':
                    self.sleep(0.02)
                    continue

                # --- INTERACTION TIMER ---
                # If interacting, wait for it to finish
                if state == 'INTERACTING':
                    # Reset Superman flags to prevent false triggers from robot movement
                    # upflag = False # Don't reset here, allows pickup during interaction
                    # downflag = False
                    
                    if current_time > state_timer:
                        state = 'AWAKE'
                        my_dog.rgb_strip.set_mode('breath', 'cyan', bps=0.5)
                        my_dog.do_action('stand', speed=80)
                        my_dog.head_move([[0,0,0]], speed=80)
                    else:
                        self.sleep(0.02)
                        continue

                # --- STATE LOGIC ---
                
                if state == 'SLEEP':
                    # Doze off animation periodically
                    if current_time - last_doze_time > 0.2:
                        my_dog.do_action('doze_off', speed=80)
                        last_doze_time = current_time
                    
                    # Wake up triggers - TOUCH ONLY
                    if is_touched:
                        print("Waking up!")
                        state = 'INTERACTING'
                        state_timer = current_time + 5
                        last_sound_time = current_time
                        
                        my_dog.body_stop()
                        my_dog.rgb_strip.set_mode('boom', 'yellow', bps=1)
                        my_dog.do_action('stand', speed=80)
                        my_dog.head_move([[0,0,0]], immediately=True, speed=80)
                        self.sleep(0.5)
                        my_dog.do_action('stretch', speed=50)

                elif state == 'AWAKE':
                    # 1. Distance (Back up)
                    # Added > 1 check from 4_response.py to avoid 0/noise
                    if distance > 1 and distance < DIST_THRESH:
                        print(f"Too close! {distance}")
                        state = 'INTERACTING'
                        state_timer = current_time + 2.5
                        
                        my_dog.rgb_strip.set_mode('bark', 'red', bps=2)
                        my_dog.do_action('backward', step_count=2, speed=95)
                        bark(my_dog, [0, 0, 0])
                        
                    # 2. Touch (Happy)
                    elif is_touched:
                        print("Touched!")
                        state = 'INTERACTING'
                        state_timer = current_time + 2
                        
                        my_dog.rgb_strip.set_mode('breath', [255, 128, 0], bps=1)
                        my_dog.do_action('wag_tail', step_count=5, speed=100)
                        bark(my_dog, [0, 0, 0])
                        my_dog.head_move([[0, 0, -20]], speed=80)

                    # 3. Sound (Turn head)
                    elif sound_dir != 0 and current_time - last_sound_time > 2:
                        print(f"Sound at {sound_dir}")
                        last_sound_time = current_time
                        state = 'INTERACTING'
                        state_timer = current_time + 2
                        
                        my_dog.rgb_strip.set_mode('listen', 'blue', bps=1)
                        yaw = -30 if sound_dir > 0 else 30
                        my_dog.head_move([[yaw, 0, 0]], speed=90)
                
                self.sleep(0.02)

        except Exception as e:
            print(f"Error in kids_play: {e}")
            import traceback
            traceback.print_exc()

example_runner = ExampleRunner()

@app.route('/examples', methods=['GET'])
def list_examples():
    # Return list of supported examples
    # Custom examples first!
    return jsonify([
        '00_kids_play.py',
        '01_super_dog.py',
        '1_wake_up.py', 
        '3_patrol.py', 
        '4_response.py',
        '5_rest.py',
        '6_be_picked_up.py',
        '8_pushup.py',
        '9_howling.py',
        '10_balance.py',
        '13_ball_track.py'
    ])

@app.route('/examples/run', methods=['POST'])
def run_example():
    data = request.json
    filename = data.get('filename')
    
    if not filename:
        return jsonify({"error": "Filename required"}), 400
        
    if not my_dog:
        return jsonify({"error": "Pidog not initialized"}), 500

    success, msg = example_runner.start(filename)
    if success:
        return jsonify({"message": msg})
    else:
        return jsonify({"error": msg}), 400

@app.route('/examples/stop', methods=['POST'])
def stop_example():
    example_runner.stop()
    return jsonify({"message": "Example stopped"})

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "online",
        "dog_initialized": my_dog is not None,
        "running_example": example_runner.running
    })

@app.route('/action', methods=['POST'])
def action():
    if example_runner.running:
        return jsonify({"error": "Cannot perform action while example is running"}), 409

    if not my_dog:
        return jsonify({"error": "Pidog not initialized"}), 500
    
    data = request.json
    name = data.get('name')
    speed = data.get('speed', 95)
    
    try:
        # Check if it's a preset action
        if hasattr(preset_actions, name):
            func = getattr(preset_actions, name)
            # Most preset actions take my_dog as first arg
            # Some take extra args, but defaults usually work
            func(my_dog)
            return jsonify({"message": f"Preset action {name} triggered"})
        
        # Check standard actions
        # Some actions might be in actions_dictionary but not directly callable via do_action if not mapped?
        # Actually do_action handles everything in actions_dictionary
        my_dog.do_action(name, speed=speed)
        return jsonify({"message": f"Action {name} triggered"})
    except Exception as e:
        print(f"Action error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/move', methods=['POST'])
def move():
    if example_runner.running:
        return jsonify({"error": "Cannot move while example is running"}), 409

    if not my_dog:
        return jsonify({"error": "Pidog not initialized"}), 500
        
    data = request.json
    direction = data.get('direction') # forward, backward, turn_left, turn_right
    speed = data.get('speed', 95)
    
    try:
        # Map directions to actions
        # 12_app_control.py uses do_action for movement too
        if direction in ['forward', 'backward', 'turn_left', 'turn_right', 'trot', 'stop']:
            if direction == 'stop':
                my_dog.body_stop()
            else:
                my_dog.do_action(direction, speed=speed)
            return jsonify({"message": f"Moving {direction}"})
        else:
            return jsonify({"error": "Invalid direction"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/head', methods=['POST'])
def head():
    if example_runner.running:
        return jsonify({"error": "Cannot move head while example is running"}), 409

    if not my_dog:
        return jsonify({"error": "Pidog not initialized"}), 500
        
    data = request.json
    yaw = data.get('yaw', 0)
    pitch = data.get('pitch', 0)
    roll = data.get('roll', 0)
    
    try:
        my_dog.head_move([[yaw, roll, pitch]], immediately=True, speed=80)
        return jsonify({"message": "Head moved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Run Flask app
    # Host 0.0.0.0 to be accessible on network
    app.run(host='0.0.0.0', port=5000, debug=False)
