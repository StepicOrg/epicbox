FROM psviderski/centos-python3
MAINTAINER Pavel Sviderski <ps@stepic.org>

# Install requirements to be able to install oslo.messaging
RUN yum install -y git gcc python34u-devel \
 && yum clean all

ADD app.tar.gz /app
WORKDIR /app

RUN pip3 install -r requirements/docker.txt

ENV EPICBOX_SETTINGS docker
CMD /app/rpcserver.py
