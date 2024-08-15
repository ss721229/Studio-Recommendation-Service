import agent_data_to_s3
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.transfers.s3_to_redshift import S3ToRedshiftOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.mysql.hooks.mysql import MySqlHook

from datetime import datetime
import logging
import os

S3_BUCKET_NAME = "team-ariel-1-bucket"
S3_DATA_DIR = "data/"
S3_AGENT_DIR = "agent/"
DOWNLOAD_DIR = "/opt/airflow/data/"

# agent 데이터를 다운로드
def download_data():
    import shutil

    agent_data_to_s3.download_agent_data(DOWNLOAD_DIR)
    logging.info("Finished download file from vworld")

    paths = agent_data_to_s3.get_csv_file_path(DOWNLOAD_DIR)

    filename = paths["csv_filename"]
    key = os.path.join(S3_DATA_DIR, paths["csv_filename"])

    agent_data_to_s3.upload_s3_and_remove(paths["csv_filepath"], key)

    os.remove(paths["zip_filepath"])
    shutil.rmtree(paths["extract_dir"])

    return filename, key


# agent 데이터 columns 변환 및 s3에 적재
def transform_and_upload_csv_to_s3(**context):
    filename, data_key = context["task_instance"].xcom_pull(key="return_value", task_ids='download_data')
    agent_data_to_s3.download_file_from_s3(data_key, DOWNLOAD_DIR)

    local_filepath = os.path.join(DOWNLOAD_DIR, filename)
    agent_data_to_s3.transform_columns(local_filepath)

    agent_key = os.path.join(S3_AGENT_DIR, filename)
    agent_data_to_s3.upload_s3_and_remove(local_filepath, agent_key)

    return agent_key


# s3에 적재한 데이터를 redshift에 적재
def load_agent_data_to_rds_from_s3(schema, table, **context):
    key = context["task_instance"].xcom_pull(key="return_value", task_ids='transform_and_upload_csv_to_s3')
    
    s3_hook = S3Hook(aws_conn_id= 's3_conn')
    file = s3_hook.download_file(key=key, bucket_name=S3_BUCKET_NAME)
    try:
        mysql = MySqlHook(mysql_conn_id='rds_conn', local_infile=True)
        conn = mysql.get_conn()
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM {schema}.{table}")
        cursor.execute(
            f"""
            LOAD DATA LOCAL INFILE '{file}'
            IGNORE
            INTO TABLE {schema}.{table}
            FIELDS TERMINATED BY ','
            LINES TERMINATED BY '\n'
            IGNORE 1 LINES
            """
        )

        cursor.close()
        conn.commit()
    finally:
        os.remove(file)



default_args = {
    'retries': 0,
}

with DAG(
    dag_id='crawling_agent_data',
    start_date=datetime(2024, 7, 1),
    catchup=False,
    schedule_interval='0 1 * * *',
    default_args=default_args,
    tags=['S3']
) as dag:

    download_data = PythonOperator(
        task_id='download_data',
        python_callable=download_data
    )

    transform_and_upload_csv_to_s3 = PythonOperator(
        task_id='transform_and_upload_csv_to_s3',
        python_callable=transform_and_upload_csv_to_s3
    )

    load_agent_data_to_redshift_from_s3 = S3ToRedshiftOperator(
        task_id="load_agent_data_to_redshift_from_s3",
        s3_bucket=S3_BUCKET_NAME,
        s3_key="{{ task_instance.xcom_pull(key='return_value', task_ids='transform_and_upload_csv_to_s3') }}",
        schema="raw_data",
        table="agency_details",
        copy_options=['csv', 'IGNOREHEADER 1'],
        redshift_conn_id="redshift_conn",
        aws_conn_id="s3_conn",
        method="REPLACE"
    )

    load_agent_data_to_rds_from_s3 = PythonOperator(
        task_id='load_agent_data_to_rds_from_s3',
        python_callable=load_agent_data_to_rds_from_s3,
        op_kwargs={
            "schema": "production",
            "table": "agency_details"
        }
    )

    download_data >> transform_and_upload_csv_to_s3 >> load_agent_data_to_redshift_from_s3 >> load_agent_data_to_rds_from_s3


# EOF