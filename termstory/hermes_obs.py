import logging
import os
import sys

logger = logging.getLogger(__name__)

def modify_config_yaml(file_path, enable=True):
    if not os.path.exists(file_path):
        if enable:
            dirname = os.path.dirname(file_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("plugins:\n  enabled:\n    - observability/nemo_relay\n")
            return True
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    plugins_idx = -1
    enabled_idx = -1
    item_idx = -1
    list_indent = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('plugins:') and not line.startswith(' '):
            plugins_idx = i
            break

    if plugins_idx != -1:
        for i in range(plugins_idx + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if not line.strip() or stripped.startswith('#'):
                continue
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                break
            if stripped.startswith('enabled:') and indent > 0:
                enabled_idx = i
                break

        if enabled_idx != -1:
            enabled_indent = len(lines[enabled_idx]) - len(lines[enabled_idx].lstrip())
            for i in range(enabled_idx + 1, len(lines)):
                line = lines[i]
                stripped = line.strip()
                if not line.strip() or stripped.startswith('#'):
                    continue
                indent = len(line) - len(line.lstrip())
                if indent <= enabled_indent:
                    break
                if stripped.startswith('- '):
                    if list_indent == -1:
                        list_indent = indent
                    if stripped == '- observability/nemo_relay':
                        item_idx = i
                        break
            if list_indent == -1:
                list_indent = enabled_indent + 2

    if enable:
        if item_idx != -1:
            return False

        if plugins_idx == -1:
            if lines and not lines[-1].endswith('\n'):
                lines.append('\n')
            lines.append("plugins:\n  enabled:\n    - observability/nemo_relay\n")
        elif enabled_idx == -1:
            plugins_indent = len(lines[plugins_idx]) - len(lines[plugins_idx].lstrip())
            indent_str = " " * (plugins_indent + 2)
            lines.insert(plugins_idx + 1, f"{indent_str}enabled:\n{indent_str}  - observability/nemo_relay\n")
        else:
            indent_str = " " * list_indent
            lines.insert(enabled_idx + 1, f"{indent_str}- observability/nemo_relay\n")

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True

    else:
        if item_idx == -1:
            return False

        lines.pop(item_idx)

        # check if enabled: list is empty
        enabled_indent = len(lines[enabled_idx]) - len(lines[enabled_idx].lstrip())
        has_other_items = False
        for i in range(enabled_idx + 1, len(lines)):
            line = lines[i]
            stripped = line.strip()
            if not line.strip() or stripped.startswith('#'):
                continue
            indent = len(line) - len(line.lstrip())
            if indent <= enabled_indent:
                break
            if stripped.startswith('- '):
                has_other_items = True
                break

        if not has_other_items:
            lines.pop(enabled_idx)

            # check if plugins: block is empty
            plugins_indent = len(lines[plugins_idx]) - len(lines[plugins_idx].lstrip())
            has_other_plugins_keys = False
            for i in range(plugins_idx + 1, len(lines)):
                line = lines[i]
                stripped = line.strip()
                if not line.strip() or stripped.startswith('#'):
                    continue
                indent = len(line) - len(line.lstrip())
                if indent <= plugins_indent:
                    break
                has_other_plugins_keys = True
                break

            if not has_other_plugins_keys:
                lines.pop(plugins_idx)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        return True


def modify_env_file(file_path, enable=True):
    if not os.path.exists(file_path):
        if enable:
            dirname = os.path.dirname(file_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("HERMES_NEMO_RELAY_ATOF_ENABLED=true\nHERMES_NEMO_RELAY_ATIF_ENABLED=true\n")
            return True
        return False

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    atof_key = "HERMES_NEMO_RELAY_ATOF_ENABLED"
    atif_key = "HERMES_NEMO_RELAY_ATIF_ENABLED"

    atof_idx = -1
    atif_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{atof_key}="):
            atof_idx = i
        elif stripped.startswith(f"{atif_key}="):
            atif_idx = i

    changed = False
    if enable:
        if atof_idx != -1:
            if lines[atof_idx].strip() != f"{atof_key}=true":
                lines[atof_idx] = f"{atof_key}=true\n"
                changed = True
        else:
            if lines and not lines[-1].endswith('\n'):
                lines.append('\n')
            lines.append(f"{atof_key}=true\n")
            changed = True

        # Re-scan to be safe
        atif_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{atif_key}="):
                atif_idx = i
                break

        if atif_idx != -1:
            if lines[atif_idx].strip() != f"{atif_key}=true":
                lines[atif_idx] = f"{atif_key}=true\n"
                changed = True
        else:
            if lines and not lines[-1].endswith('\n'):
                lines.append('\n')
            lines.append(f"{atif_key}=true\n")
            changed = True
    else:
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{atof_key}=") or stripped.startswith(f"{atif_key}="):
                changed = True
                continue
            new_lines.append(line)
        lines = new_lines

    if changed:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    return changed


def run_toggle(env_path=None, config_path=None):
    if env_path is None:
        env_path = os.path.expanduser("~/.hermes/.env")
    if config_path is None:
        config_path = os.path.expanduser("~/.hermes/config.yaml")

    try:
        response = input("Enable DeepWiki observability? (y/n): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return

    if response in ("y", "yes"):
        env_changed = modify_env_file(env_path, enable=True)
        config_changed = modify_config_yaml(config_path, enable=True)

        print("\nDeepWiki observability has been enabled.")
        print("Changes made:")

        if env_changed:
            print(f"- Added/Updated HERMES_NEMO_RELAY_ATOF_ENABLED=true in {env_path}")
            print(f"- Added/Updated HERMES_NEMO_RELAY_ATIF_ENABLED=true in {env_path}")
        else:
            print("- Environment variables already configured.")

        if config_changed:
            print(f"- Added 'observability/nemo_relay' to plugins.enabled in {config_path}")
        else:
            print("- Plugin 'observability/nemo_relay' already enabled in config.")

        print("\nTo revert these changes, run this command again and select 'n'.")

    elif response in ("n", "no"):
        env_changed = modify_env_file(env_path, enable=False)
        config_changed = modify_config_yaml(config_path, enable=False)

        print("\nDeepWiki observability has been disabled.")
        print("Changes made:")

        if env_changed:
            print(
                f"- Removed HERMES_NEMO_RELAY_ATOF_ENABLED and HERMES_NEMO_RELAY_ATIF_ENABLED from {env_path}"
            )
        else:
            print("- Environment variables were not present or already removed.")

        if config_changed:
            print(f"- Removed 'observability/nemo_relay' from plugins.enabled in {config_path}")
        else:
            print("- Plugin 'observability/nemo_relay' was not enabled or already removed.")
    else:
        print("Invalid input. Please enter 'y' or 'n'.")