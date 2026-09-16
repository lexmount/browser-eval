"""Shared investigation signals; these do not change official scores."""
import re

ERROR_PAGE = re.compile(
    r"(?:^#\s*Error response\b|^Error code:\s*404\b|ERR[_\\]ACCESS[_\\]DENIED\b|"
    r"^#\s*无法访问此网站\b|^This site can.t be reached\b)", re.I | re.M,
)


def require_valid_teds_for_release(records):
    """A reproduced official defect may be inspected locally, never released."""
    invalid = []
    for row in records:
        details = row.get('metrics', {}).get('table_TEDS', {}).get('details', {})
        if all(key in details for key in ('predicted_nodes', 'groundtruth_nodes', 'edit_distance')):
            lower_bound = abs(details['predicted_nodes'] - details['groundtruth_nodes'])
            if details['edit_distance'] + 1e-9 < lower_bound:
                invalid.append(row.get('attempt_id', row.get('case_id', 'unknown')))
    if invalid:
        raise ValueError(f'Publication blocked: {len(invalid)} impossible TEDS distances; '
                         'resolve the scorer defect and revalidate affected scores before release')
