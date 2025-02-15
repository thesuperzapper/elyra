#
# Copyright 2018-2022 Elyra Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Operator source: https://raw.githubusercontent.com/apache/airflow/1.10.15/airflow/operators/bash_operator.py
# This file has been modified from its original source to ensure it passes
# Elyra linting. No functionality changes have been made.
#


from builtins import bytes
import os
import signal
from subprocess import PIPE
from subprocess import Popen
from subprocess import STDOUT
from tempfile import gettempdir
from tempfile import NamedTemporaryFile

from airflow.exceptions import AirflowException
from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults
from airflow.utils.file import TemporaryDirectory
from airflow.utils.operator_helpers import context_to_airflow_vars


class BashOperator(BaseOperator):
    r"""
    Execute a Bash script, command or set of commands.

    .. seealso::
        For more information on how to use this operator, take a look at the guide:
        :ref:`howto/operator:BashOperator`

    :param bash_command: The command, set of commands or reference to a
        bash script (must be '.sh') to be executed. (templated)
    :type bash_command: str
    :param xcom_push: If xcom_push is True, the last line written to stdout
        will also be pushed to an XCom when the bash command completes.
    :type xcom_push: bool
    :param env: If env is not None, it must be a mapping that defines the
        environment variables for the new process; these are used instead
        of inheriting the current process environment, which is the default
        behavior. (templated)
    :type env: dict
    :param output_encoding: Output encoding of bash command
    :type output_encoding: str

    .. warning::

        Care should be taken with "user" input or when using Jinja templates in the
        ``bash_command``, as this bash operator does not perform any escaping or
        sanitization of the command.

        This applies mostly to using "dag_run" conf, as that can be submitted via
        users in the Web UI. Most of the default template variables are not at
        risk.

    For example, do **not** do this:

    .. code-block:: python

        bash_task = BashOperator(
            task_id="bash_task",
            bash_command='echo "Here is the message: \'{{ dag_run.conf["message"] if dag_run else "" }}\'"',
        )

    Instead, you should pass this via the ``env`` kwarg and use double-quotes
    inside the bash_command, as below:

    .. code-block:: python

        bash_task = BashOperator(
            task_id="bash_task",
            bash_command='echo "here is the message: \'$message\'"',
            env={'message': '{{ dag_run.conf["message"] if dag_run else "" }}'},
        )

    """
    template_fields = ("bash_command", "env")
    template_ext = (
        ".sh",
        ".bash",
    )
    ui_color = "#f0ede4"

    @apply_defaults
    def __init__(self, bash_command, xcom_push=False, env=None, output_encoding="utf-8", *args, **kwargs):

        super(BashOperator, self).__init__(*args, **kwargs)
        self.bash_command = bash_command
        self.env = env
        self.xcom_push_flag = xcom_push
        self.output_encoding = output_encoding
        self.sub_process = None

    def execute(self, context):
        """
        Execute the bash command in a temporary directory
        which will be cleaned afterwards
        """
        self.log.info(f"Tmp dir root location: \n {gettempdir()}")

        # Prepare env for child process.
        env = self.env
        if env is None:
            env = os.environ.copy()
        airflow_context_vars = context_to_airflow_vars(context, in_env_var_format=True)
        acv_log = "\n".join([f"{k}={v}" for k, v in airflow_context_vars.items()])
        self.log.debug("Exporting the following env vars:\n" f"{acv_log}")
        env.update(airflow_context_vars)

        self.lineage_data = self.bash_command

        with TemporaryDirectory(prefix="airflowtmp") as tmp_dir:
            with NamedTemporaryFile(dir=tmp_dir, prefix=self.task_id) as f:

                f.write(bytes(self.bash_command, "utf_8"))
                f.flush()
                fname = f.name
                script_location = os.path.abspath(fname)
                self.log.info(f"Temporary script location: {script_location}")

                def pre_exec():
                    # Restore default signal disposition and invoke setsid
                    for sig in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                        if hasattr(signal, sig):
                            signal.signal(getattr(signal, sig), signal.SIG_DFL)
                    os.setsid()

                self.log.info(f"Running command: {self.bash_command}")
                self.sub_process = Popen(
                    ["bash", fname], stdout=PIPE, stderr=STDOUT, cwd=tmp_dir, env=env, preexec_fn=pre_exec
                )

                self.log.info("Output:")
                line = ""
                for line in iter(self.sub_process.stdout.readline, b""):
                    line = line.decode(self.output_encoding).rstrip()
                    self.log.info(line)
                self.sub_process.wait()
                self.log.info(f"Command exited with return code {self.sub_process.returncode}")

                if self.sub_process.returncode:
                    raise AirflowException("Bash command failed")

        if self.xcom_push_flag:
            return line

    def on_kill(self):
        self.log.info("Sending SIGTERM signal to bash process group")
        if self.sub_process and hasattr(self.sub_process, "pid"):
            os.killpg(os.getpgid(self.sub_process.pid), signal.SIGTERM)
