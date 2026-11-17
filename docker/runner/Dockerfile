# One image for the whole project: Apache Airflow (orchestration) with a
# separate virtual environment for the pipeline, so that the data and ML
# libraries do not have to follow the version constraints of Airflow itself.
FROM apache/airflow:2.10.5-python3.12

USER root
RUN python -m venv /opt/bikpi \
    && mkdir -p /opt/mlflow \
    && chown -R airflow: /opt/bikpi /opt/mlflow
USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN /opt/bikpi/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && /opt/bikpi/bin/pip freeze > /opt/bikpi/requirements.lock.txt

ENV PYTHONPATH=/opt/project/src \
    BIKPI_PYTHON=/opt/bikpi/bin/python
WORKDIR /opt/project
