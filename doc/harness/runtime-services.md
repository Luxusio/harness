# Runtime Services

Harness can start project runtime dependencies in the background before live QA.
The manifest field is `runtime.services[]`.
Declare them in `doc/harness/manifest.yaml`:

```yaml
runtime:
  services:
    - name: api
      command: ./gradlew bootRun
      cwd: services/api
      healthcheck: curl -fsS http://localhost:8080/actuator/health
      ready_timeout_sec: 60
      stop: interrupt
      self_heal:
        - ./gradlew --stop
        - ./gradlew clean assemble
```

Commands:

```bash
python3 plugin/scripts/runtime_services.py start
python3 plugin/scripts/runtime_services.py status
python3 plugin/scripts/runtime_services.py logs api
python3 plugin/scripts/runtime_services.py stop
```

Fields:

- `name`: stable service id used by `start api`, `logs api`, and state.
- `command`: background command to start the service.
- `cwd`: optional working directory.
- `healthcheck`: shell command that exits `0` when the service is ready.
- `ready_timeout_sec`: readiness wait, default `30`.
- `stop_command`: optional graceful stop command. Without it, harness sends
  SIGTERM then SIGKILL to the process group it started.
- `self_heal`: optional bounded list of shell commands to run after a failed
  start/healthcheck attempt. Harness restarts the service after each command.
- `install_command`, `repair_command`, `heal_command`: single-command aliases
  appended to `self_heal`.
- `restart_on_fail`: defaults to `true` and performs one bounded restart when
  no self-heal commands are declared.

State lives at `doc/harness/runtime/services.json`; logs live under
`doc/harness/runtime/logs/`. QA agents use this state as evidence for live
HTTP/browser verification. A service that stays unhealthy becomes a visible
`BLOCKED_ENV` reason rather than a silent downgrade to unit or mock tests.
