# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_mcp.config import find_config, load_config
from hpc_mcp.errors import ConfigError

from conftest import config_document


def write_config(directory: Path, document: dict) -> Path:
    path = directory / ".hpc-assistant.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class TestLoading:
    def test_a_complete_config_loads(self, tmp_path):
        config = load_config(write_config(tmp_path, config_document()))
        assert config.remote.host == "lumi"
        assert config.remote.project_root == "/scratch/project_465000000/work"
        assert config.preamble.modules == ("LUMI/24.03", "rocm/6.0.3")
        assert config.preamble.env == (("MY_VAR", "value with spaces"),)
        assert config.limits.max_find_depth == 3

    def test_comment_keys_from_the_template_are_ignored(self, tmp_path):
        document = config_document()
        document["_comment"] = "explanatory text"
        document["remote"]["_comment"] = "more explanatory text"
        # Per-field annotations too, so the template can explain one setting in place.
        document["preamble"]["_comment_apply_to_scheduler"] = "why this exists"
        config = load_config(write_config(tmp_path, document))
        assert config.remote.host == "lumi"
        assert config.preamble.apply_to_scheduler is False

    def test_the_config_is_found_by_walking_up_from_a_subdirectory(self, tmp_path):
        write_config(tmp_path, config_document())
        nested = tmp_path / "src" / "kernels"
        nested.mkdir(parents=True)
        assert find_config(nested) == tmp_path / ".hpc-assistant.json"

    def test_a_missing_config_says_how_to_create_one(self, tmp_path):
        with pytest.raises(ConfigError) as caught:
            find_config(tmp_path)
        assert "hpc-assistant-init --local" in str(caught.value)

    def test_invalid_json_is_reported_as_such(self, tmp_path):
        path = tmp_path / ".hpc-assistant.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError) as caught:
            load_config(path)
        assert "not valid JSON" in str(caught.value)


class TestValidation:
    def test_a_user_at_host_remote_is_refused(self, tmp_path):
        document = config_document()
        document["remote"]["host"] = "user@lumi.csc.fi"
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        # The reason matters more than the refusal: an alias keeps authentication in the
        # user's ssh-agent, which is the promise the whole design rests on.
        assert "ssh-agent" in str(caught.value)

    def test_a_relative_project_root_is_refused(self, tmp_path):
        document = config_document()
        document["remote"]["project_root"] = "work/project"
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, document))

    @pytest.mark.parametrize("root", ["$SCRATCH/work", "~/work"])
    def test_an_unexpanded_project_root_is_refused(self, tmp_path, root):
        document = config_document()
        document["remote"]["project_root"] = root
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "concrete path" in str(caught.value)

    def test_a_missing_project_root_is_refused(self, tmp_path):
        document = config_document()
        del document["remote"]["project_root"]
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, document))

    def test_a_missing_partition_is_refused(self, tmp_path):
        document = config_document()
        del document["slurm"]["default_partition"]
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "default_partition" in str(caught.value)
        assert "small-g" not in str(caught.value)

    def test_an_absent_slurm_section_does_not_inherit_a_lumi_partition(self, tmp_path):
        document = config_document()
        del document["slurm"]
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "default_partition" in str(caught.value)

    def test_extra_directives_are_loaded(self, tmp_path):
        document = config_document()
        document["slurm"]["extra_directives"] = ["--gres=gpu:a100:1", "--mem=32G"]
        config = load_config(write_config(tmp_path, document))
        assert config.slurm.extra_directives == ("--gres=gpu:a100:1", "--mem=32G")
        assert config.slurm.partition == "small-g"

    def test_extra_directives_must_look_like_sbatch_options(self, tmp_path):
        document = config_document()
        document["slurm"]["extra_directives"] = ["rm -rf /"]
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "sbatch options" in str(caught.value)

    def test_login_commands_and_env_probes_are_site_configurable(self, tmp_path):
        document = config_document()
        document["login_commands"] = ["sbalance", "myquota"]
        document["env_probes"] = ["CUDA_HOME"]
        config = load_config(write_config(tmp_path, document))
        assert config.login_commands == ("sbalance", "myquota")
        assert config.env_probes == ("CUDA_HOME",)

    def test_env_probes_default_to_rocm_path(self, tmp_path):
        config = load_config(write_config(tmp_path, config_document()))
        assert config.env_probes == ("ROCM_PATH",)

    def test_env_probes_can_be_cleared(self, tmp_path):
        document = config_document()
        document["env_probes"] = []
        config = load_config(write_config(tmp_path, document))
        assert config.env_probes == ()

    def test_the_preamble_defaults_to_staying_out_of_scheduler_calls(self, tmp_path):
        config = load_config(write_config(tmp_path, config_document()))
        assert config.preamble.apply_to_scheduler is False

    def test_a_site_can_ask_for_the_preamble_on_scheduler_calls(self, tmp_path):
        document = config_document()
        document["preamble"]["apply_to_scheduler"] = True
        config = load_config(write_config(tmp_path, document))
        assert config.preamble.apply_to_scheduler is True

    def test_a_non_boolean_apply_to_scheduler_is_refused(self, tmp_path):
        document = config_document()
        document["preamble"]["apply_to_scheduler"] = "yes"
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "true or false" in str(caught.value)

    def test_an_unknown_file_access_mode_is_refused(self, tmp_path):
        document = config_document()
        document["file_access"]["mode"] = "mutagen"
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "rsync" in str(caught.value)

    # git and sshfs were designed but never verified end to end, so they are rejected
    # rather than half-supported. A config written for one of them must fail loudly.
    @pytest.mark.parametrize("mode", ["git", "sshfs"])
    def test_an_unsupported_file_access_mode_is_refused(self, tmp_path, mode):
        document = config_document()
        document["file_access"] = {"mode": mode}
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert mode in str(caught.value)

    @pytest.mark.parametrize("value", [0, -1, "many", None])
    def test_a_non_positive_limit_is_refused(self, tmp_path, value):
        document = config_document()
        document["limits"]["max_read_bytes"] = value
        with pytest.raises(ConfigError) as caught:
            load_config(write_config(tmp_path, document))
        assert "max_read_bytes" in str(caught.value)

    def test_backoff_bounds_must_be_ordered(self, tmp_path):
        document = config_document()
        document["limits"]["await_job_initial_interval_seconds"] = 120
        document["limits"]["await_job_max_interval_seconds"] = 30
        with pytest.raises(ConfigError):
            load_config(write_config(tmp_path, document))

    def test_a_trailing_slash_on_the_project_root_is_normalised(self, tmp_path):
        document = config_document()
        document["remote"]["project_root"] = "/scratch/project_465000000/work/"
        config = load_config(write_config(tmp_path, document))
        assert config.remote.project_root == "/scratch/project_465000000/work"


class TestDerivedPaths:
    def test_the_audit_log_is_relative_to_the_project(self, tmp_path):
        config = load_config(write_config(tmp_path, config_document()))
        assert config.audit_log_path == tmp_path / ".hpc-assistant" / "audit.log"

    def test_an_absolute_audit_log_is_left_alone(self, tmp_path):
        document = config_document()
        document["audit"] = {"log": "/tmp/hpc-audit.log"}
        config = load_config(write_config(tmp_path, document))
        assert config.audit_log_path == Path("/tmp/hpc-audit.log")

    def test_local_root_defaults_to_the_config_directory(self, tmp_path):
        document = config_document()
        document["file_access"] = {"mode": "rsync"}
        config = load_config(write_config(tmp_path, document))
        assert config.file_access.local_root == str(tmp_path)
