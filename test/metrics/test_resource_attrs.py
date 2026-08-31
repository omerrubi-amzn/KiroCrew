"""``_resource_attributes`` — identity, version, and environment labels.

Every attribute asserted here becomes a label on EVERY exported series, so
these tests pin four properties: the values are clamped/bounded (a raw
dev-build version, a patch-level runtime version, or a pass-through exotic
architecture would mint unbounded series), every probe fails soft (a broken
probe omits its attribute, never loses telemetry), the synchronous build path
never CREATES the install id (that is file I/O and the first build can run on
the event loop — creation belongs to the consent-worker rebuild, which always
runs on its own thread), and the deliberately-absent attributes stay absent
(the distribution channel was removed from the beacon by a data-minimization
pass; re-adding it here would quietly undo that decision).
"""

import json
import os
import platform
import re
import sys

import kiro_crew
from kiro_crew import beacon
from kiro_crew.config.loader import KiroCrewConfig, TelemetryConfig
from kiro_crew.metrics import provider
from kiro_crew.metrics.provider import get_recorder, reset_for_testing
from kiro_crew.metrics.provider import shutdown as provider_shutdown


def _patch_config(monkeypatch, **tel_kwargs):
    fake = KiroCrewConfig(telemetry=TelemetryConfig(**tel_kwargs))
    monkeypatch.setattr(KiroCrewConfig, "load", classmethod(lambda cls: fake))
    monkeypatch.delenv("KIROCREW_TELEMETRY", raising=False)


def test_attrs_carry_identity_version_and_environment():
    # The build path is read-only for the id, so materialize it first — the
    # way every non-fresh install already has it on disk.
    beacon.install_id(create=True)
    attrs = provider._resource_attributes()

    assert attrs["service.name"] == "kirocrew"

    # Release-clamped, never a raw dev/nightly stamp: same clamp the beacon
    # ships, so the two surfaces can never disagree about one build.
    assert attrs["service.version"] == beacon.release(kiro_crew.__version__)
    version = str(attrs["service.version"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", version) or version == beacon.UNKNOWN_VERSION

    assert attrs["os.type"] == platform.system().lower()
    assert attrs["process.runtime.name"] == platform.python_implementation().lower()
    # major.minor ONLY — the patch level would add cardinality without
    # answering anything the minor does not.
    assert attrs["process.runtime.version"] == (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
    assert re.fullmatch(r"\d+\.\d+", str(attrs["process.runtime.version"]))

    # The core count is what lets cpu.seconds become a machine percentage
    # downstream; it exists only client-side.
    assert attrs["host.cpu.logical_count"] == os.cpu_count()
    assert isinstance(attrs["host.cpu.logical_count"], int)

    # Stable install id, not the SDK's per-process UUID — and the process
    # identity travels SEPARATELY so concurrent processes of one install
    # cannot interleave their series at a backend.
    install_id = str(attrs["service.instance.id"])
    assert len(install_id) == 32
    assert all(c in "0123456789abcdef" for c in install_id)
    assert attrs["process.pid"] == os.getpid()
    assert isinstance(attrs["process.pid"], int)


def test_arch_aliases_fold_to_one_label():
    # One architecture must not appear as two labels across OSes.
    assert provider._ARCH_BY_MACHINE["x86_64"] == "amd64"
    assert provider._ARCH_BY_MACHINE["amd64"] == "amd64"
    assert provider._ARCH_BY_MACHINE["aarch64"] == "arm64"
    assert provider._ARCH_BY_MACHINE["arm64"] == "arm64"
    machine = platform.machine().lower()
    expected = provider._ARCH_BY_MACHINE.get(machine, provider._ATTR_OTHER)
    assert provider._resource_attributes().get("host.arch") == expected


def test_unknown_environment_readings_fold_to_other(monkeypatch):
    # The docstring promises CLOSED sets: an exotic platform must fold to the
    # shared "other" bucket, never pass its own spelling through as a label.
    monkeypatch.setattr(provider.platform, "system", lambda: "SunOS")
    monkeypatch.setattr(provider.platform, "machine", lambda: "riscv64")
    attrs = provider._resource_attributes()
    assert attrs["os.type"] == provider._ATTR_OTHER
    assert attrs["host.arch"] == provider._ATTR_OTHER


def test_version_probe_failure_omits_only_that_attribute(monkeypatch):
    def _boom(_version):
        raise RuntimeError("release parse failed")

    monkeypatch.setattr(beacon, "release", _boom)
    attrs = provider._resource_attributes()
    assert "service.version" not in attrs
    # The other groups are untouched by the failed probe.
    assert attrs["service.name"] == "kirocrew"
    assert "os.type" in attrs


def test_id_read_failure_omits_the_attribute(monkeypatch):
    def _boom(*, create=True):
        raise OSError("disk unreadable")

    monkeypatch.setattr(beacon, "install_id", _boom)
    attrs = provider._resource_attributes()
    assert "service.instance.id" not in attrs
    assert "service.version" in attrs


def test_build_path_is_read_only_and_worker_mint_precedes_rebuild():
    # Fresh install (conftest-isolated home, no id file): _resource_attributes
    # must NOT create the id — the first build can run on the event loop and
    # creation is mkdir + mkstemp + link. It omits the attribute and leaves
    # the disk untouched. The consent worker mints (beacon.install_id
    # create=True, the call it performs before rebuilding) and the same
    # read-only path then picks the id up.
    assert beacon.install_id(create=False) == ""

    attrs = provider._resource_attributes()
    assert "service.instance.id" not in attrs
    assert beacon.install_id(create=False) == "", "read-only path created the id"

    beacon.install_id(create=True)  # what _consent_worker does before rebuilding
    assert "service.instance.id" in provider._resource_attributes()


def test_pre_enabled_fresh_install_backfills_the_id(tmp_path, monkeypatch):
    # The gap GPT round 3 named: telemetry enabled from the very first boot
    # (config pre-enabled, beacon disabled — a container image), so consent
    # never flips and the consent worker would never rebuild. The one-shot
    # backfill must mint the id and rebuild WITHOUT a consent change.
    import time as _time

    reset_for_testing()
    _patch_config(
        monkeypatch,
        enabled=True,
        local_dir=str(tmp_path),
        export_interval_seconds=3600,
    )
    try:
        assert beacon.install_id(create=False) == ""

        rec = get_recorder()  # first build: live, id-less, backfill armed
        assert rec.enabled is True
        assert provider._id_backfill_pending is True

        get_recorder()  # schedules the worker on the locked branch
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            if not provider._id_backfill_pending and beacon.install_id(create=False):
                break
            # The backfill is a HOT swap: at no point during it may a caller
            # be handed a no-op recorder (that would silently discard live
            # metrics on a path where consent never moved).
            assert get_recorder().enabled is True
            _time.sleep(0.02)
        assert beacon.install_id(create=False), "backfill never minted the id"
        assert provider._id_backfill_pending is False

        # The rebuilt recorder's exported resource carries the identity.
        get_recorder().counter("kirocrew.session.idle_expired", attrs={"turn_active": False})
        provider_shutdown()
        shards = sorted(tmp_path.glob("metrics-*.jsonl"))
        assert shards
        last = json.loads(shards[-1].read_text().splitlines()[-1])
        resource_attrs = last["resource_metrics"][0]["resource"]["attributes"]
        assert "service.instance.id" in resource_attrs
    finally:
        reset_for_testing()


def test_failed_backfill_rebuild_keeps_the_healthy_recorder(tmp_path, monkeypatch):
    # A transient rebuild failure during the backfill must NOT swap the
    # healthy id-less recorder for a dead one: a missing label is strictly
    # better than silently discarding every subsequent metric. The one-shot
    # flag is spent either way, so the failure cannot churn rebuilds.
    import time as _time

    reset_for_testing()
    _patch_config(
        monkeypatch,
        enabled=True,
        local_dir=str(tmp_path),
        export_interval_seconds=3600,
    )
    try:
        assert beacon.install_id(create=False) == ""
        rec = get_recorder()  # healthy, id-less, backfill armed
        assert rec.enabled is True
        assert provider._id_backfill_pending is True

        def _broken_build():
            return provider._Build(provider.MetricsRecorder(None), None, True)

        monkeypatch.setattr(provider, "_build_recorder", _broken_build)
        get_recorder()  # schedules the worker, which hits the broken build
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            if provider._id_backfill_attempted and not provider._check_in_flight:
                break
            _time.sleep(0.02)
        assert provider._id_backfill_attempted is True

        # The dead build was not installed: the same healthy recorder serves.
        assert get_recorder() is rec
        assert get_recorder().enabled is True
    finally:
        reset_for_testing()


def test_channel_and_install_type_stay_absent():
    # Deliberate exclusions (see the function docstring): the distribution
    # channel narrows the anonymity crowd a stable id hides in, and there is
    # no reliable install-type detection. Their absence is a decision, so it
    # gets a regression guard.
    attrs = provider._resource_attributes()
    for key in attrs:
        lowered = key.lower()
        assert "channel" not in lowered
        assert "distribution" not in lowered
        assert "install_type" not in lowered


def test_exported_shard_carries_the_resource(tmp_path, monkeypatch):
    beacon.install_id(create=True)
    reset_for_testing()
    _patch_config(
        monkeypatch,
        enabled=True,
        local_dir=str(tmp_path),
        export_interval_seconds=3600,
    )
    try:
        rec = get_recorder()
        assert rec.enabled is True
        rec.counter("kirocrew.session.idle_expired", attrs={"turn_active": False})
        # shutdown() performs the final flush on the calling thread.
        provider_shutdown()

        shards = sorted(tmp_path.glob("metrics-*.jsonl"))
        assert shards, "no shard written by the final flush"
        record = json.loads(shards[0].read_text().splitlines()[0])
        resource_attrs = record["resource_metrics"][0]["resource"]["attributes"]
        assert resource_attrs["service.name"] == "kirocrew"
        assert resource_attrs["service.version"] == beacon.release(kiro_crew.__version__)
        assert resource_attrs["os.type"] == platform.system().lower()
        assert resource_attrs["host.cpu.logical_count"] == os.cpu_count()
        assert resource_attrs["process.pid"] == os.getpid()
        assert "service.instance.id" in resource_attrs
    finally:
        reset_for_testing()
