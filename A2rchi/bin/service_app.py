#!/bin/python
from A2rchi.app.app import FlaskAppWrapper

from flask import Flask


app = FlaskAppWrapper(Flask(
    __name__,
    template_folder="A2rchi/app/templates",
    static_folder="A2rchi/app/static",
))
app.run(debug=True, port=5000, host="localhost", ssl_context="adhoc")
