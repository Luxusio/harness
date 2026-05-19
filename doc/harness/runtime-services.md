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
      env_file: .env.test
      required_env: [DATABASE_URL]
      port: 8080
      healthcheck: curl -fsS http://localhost:8080/actuator/health
      ready_timeout_sec: 60
      stop: interrupt
      self_heal:
        - ./gradlew --stop
        - ./gradlew clean assemble
      seed_command: ./gradlew dbSeed
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
- `env_file`: optional dotenv-style file loaded into the service process and
  healthcheck environment. Relative paths resolve under `cwd`.
- `required_env` / `env_required`: environment variables that must be present
  in the process environment or `env_file` before startup.
- `env_setup_command`: bounded command run once when `required_env` is missing;
  use it to generate a dev-only env file or token.
- `port` / `host`: optional preflight port check. If the port already accepts
  connections before startup, the service blocks unless `kill_port_on_conflict`
  is explicitly true.
- `kill_port_on_conflict`: opt-in cleanup for local port conflicts. Uses `lsof`
  when available; otherwise the service remains blocked with a clear reason.
- `healthcheck`: shell command that exits `0` when the service is ready.
- `ready_timeout_sec`: readiness wait, default `30`.
- `stop_command`: optional graceful stop command. Without it, harness sends
  SIGTERM then SIGKILL to the process group it started.
- `self_heal`: optional bounded list of shell commands to run after a failed
  start/healthcheck attempt. Harness restarts the service after each command.
- `install_command`, `repair_command`, `seed_command`, `heal_command`:
  single-command aliases appended to `self_heal`.
- `restart_on_fail`: defaults to `true` and performs one bounded restart when
  no self-heal commands are declared.

State lives at `doc/harness/runtime/services.json`; logs live under
`doc/harness/runtime/logs/`. QA agents use this state as evidence for live
HTTP/browser verification. A service that stays unhealthy becomes a visible
`BLOCKED_ENV` reason rather than a silent downgrade to unit or mock tests.
Blocked service state includes `failure_class`, `recommended_action`, and
`last_log_excerpt` so agents can distinguish missing dependencies, port
conflicts, missing env, migration/seed gaps, and unknown failures without
re-reading the entire log.
