FROM python:3.5

RUN mkdir /app
WORKDIR /app

ADD requirements.txt .
RUN pip install -r requirements.txt

ADD . /app/

ENV AWS_ACCESS_KEY_ID id
ENV AWS_SECRET_ACCESS_KEY key
ENV AWS_DEFAULT_REGION region
ENV AWS_ROUTE53_ZONE zone_id
ENV AWS_ROUTE53_DOMAIN foo.bar
ENV AWS_AUTOSCALING_GROUP foo
ENV FLYNN_PIN pin
ENV FLYNN_KEY key
ENV SSH_KEY key

ENTRYPOINT ['celery']