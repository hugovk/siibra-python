FROM python:3.8-alpine

RUN python -m pip install -U pip

ADD . /siibra-python
WORKDIR /siibra-python

RUN pip install -U .

# HBP_AUTH_TOKEN

