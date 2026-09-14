# Date-based version: YYYYMMDD.N, where N counts releases within a day
# starting at 0. No semantic versioning. Bump the date on any release day;
# bump N only for a second release on the same day.
#
# This string also becomes CFBundleVersion / CFBundleShortVersionString in
# the .app bundle (period-separated integers, which this satisfies) and the
# `stickies2md --version` output.
__version__ = "20260913.4"

# End of File #
