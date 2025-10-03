# syntax=docker/dockerfile:1
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel
RUN mkdir -p /root/A2rchi
WORKDIR /root/A2rchi

COPY requirements.txt requirements.txt
COPY LICENSE LICENSE

RUN pip install --upgrade pip && pip install -r requirements.txt
