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

    Vilib = MockVilib

app = Flask(__name__)
CORS(app)

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

@app.route('/status', methods=['GET'])
def status():
    return jsonify({
        "status": "online",
        "dog_initialized": my_dog is not None
    })

@app.route('/action', methods=['POST'])
def action():
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
