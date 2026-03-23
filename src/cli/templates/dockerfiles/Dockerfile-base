# syntax=docker/dockerfile:1
# FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-devel
FROM docker.io/library/python:3.11
RUN mkdir -p /root/archi
WORKDIR /root/archi

COPY requirements.txt requirements.txt
COPY LICENSE LICENSE

RUN pip install --upgrade pip && pip install -r requirements.txt
