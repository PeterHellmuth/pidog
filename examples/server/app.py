from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from pidog import Pidog
from vilib import Vilib
import time
import threading
import logging

app = Flask(__name__)
CORS(app)

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
        if name == 'bark':
            # Bark logic from examples
            from pidog.pidog import Pidog
            # We might need to import specific bark functions if they aren't methods of Pidog
            # Checking 12_app_control.py, bark seems to be a custom function or action
            # my_dog.do_action('bark') might work if it's in actions_dictionary, 
            # but 12_app_control.py uses a lambda calling `bark(my_dog, ...)`
            # Let's try do_action first if it exists, otherwise we might need to implement bark manually
            # For now, let's assume standard actions work via do_action
            my_dog.do_action(name, speed=speed)
        else:
            my_dog.do_action(name, speed=speed)
            
        return jsonify({"message": f"Action {name} triggered"})
    except Exception as e:
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
