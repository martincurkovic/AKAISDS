"""Text for the MIDI loopback test: what the Results window shows and what a user shares.

Pure functions (no Qt, no MIDI) so they are easy to test. `results` is the list of
`(byte_count, passed, detail)` tuples the Settings dialog collects."""

import platform
from urllib.parse import quote

GITHUB_ISSUES_URL = "https://github.com/martincurkovic/AKAISDS/issues/new"


def describe_os():
    system = platform.system()
    if system == "Darwin":
        version = platform.mac_ver()[0]
        return f"macOS {version}" if version else "macOS"
    if system == "Linux":
        try:
            return platform.freedesktop_os_release().get("PRETTY_NAME") or "Linux"
        except OSError:
            return "Linux"
    return platform.platform() or system or "Unknown"


def _port_text(input_name, output_name):
    if input_name == output_name:
        return [f"MIDI interface: {input_name}"]
    return [f"MIDI input: {input_name}", f"MIDI output: {output_name}"]


def header_lines(input_name, output_name, os_name, app_version):
    return [
        *_port_text(input_name, output_name),
        f"Operating system: {os_name}",
        f"AKAISDS version: {app_version}",
    ]


def result_lines(results):
    lines = []
    for size, passed, detail in results:
        lines.append(f"{size:>5} bytes: {'PASS' if passed else 'FAIL'}" + ("" if passed else f" - {detail}"))
    return lines


def verdict(results):
    failed = [size for size, passed, _ in results if not passed]
    if not failed:
        return "All sizes tested passed cleanly."
    return (
        f"This interface starts failing around {failed[0]} bytes. "
        "Real MIDI SDS data packets are 127 bytes, and larger responses "
        "(like a sample list with many samples) can be well over 1000 bytes - "
        "if failures start below that, this interface may struggle with real transfers too."
    )


def report_text(results, input_name, output_name, os_name, app_version):
    """Everything worth sending, as plain text (header, per-size results, verdict)."""
    return "\n".join(
        [*header_lines(input_name, output_name, os_name, app_version), "", *result_lines(results), "", verdict(results)]
    )


def issue_url(results, input_name, output_name, os_name, app_version):
    """A GitHub 'new issue' link with the title and body filled in (the user still presses Submit)."""
    interface = input_name if input_name == output_name else f"{input_name} / {output_name}"
    body = "Loopback test results from AKAISDS:\n\n```\n" + report_text(
        results, input_name, output_name, os_name, app_version
    ) + "\n```\n"
    return f"{GITHUB_ISSUES_URL}?title={quote('MIDI interface test: ' + interface)}&body={quote(body)}"
