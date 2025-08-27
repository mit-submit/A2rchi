# syntax=docker/dockerfile:1
# FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-devel
FROM python:3.10
RUN mkdir -p /root/A2rchi
WORKDIR /root/A2rchi

COPY requirements.txt requirements.txt
COPY LICENSE LICENSE

RUN pip install --upgrade pip && pip install -r requirements.txt
