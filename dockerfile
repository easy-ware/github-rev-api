FROM python:3.12-slim


WORKDIR /ghserver


COPY . /ghserver


RUN python3 -m pip install -r requirements/req.txt
EXPOSE 80
CMD ["python3" , "/ghserver/ghserver.py"]