from flask import Flask, request, jsonify
from flask_cors import CORS  # Import CORS from flask_cors
import numpy as np

app = Flask(__name__)
CORS(app)

def get_response(user_text):
    return "THIS IS A FILLER RESPONSE: " + str(np.random.randint(10000,99999))

@app.route('/get_chat_response', methods=['POST'])
def get_chat_response():
    user_text = request.json.get('user_text')  # Get user input from the request
    response = get_response(user_text)  # Call your Python function
    return jsonify({'response': response})

if __name__ == '__main__':
    app.run(debug=True, port=7680)