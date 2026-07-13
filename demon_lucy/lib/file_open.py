from __future__ import annotations

import errno
import os

from demon_lucy.lib.runtime_system import RuntimeSystem


def _open_windows_file_no_follow(path_value: str, flags: int) -> int:
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
    except (ImportError, AttributeError) as error:
        raise OSError(
            errno.ENOTSUP,
            "secure Windows file opening is unavailable",
            path_value,
        ) from error

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_read_attributes = 0x0080
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    create_new = 1
    open_existing = 3
    open_always = 4
    file_attribute_normal = 0x00000080
    file_attribute_reparse_point = 0x00000400
    file_flag_open_reparse_point = 0x00200000
    file_attribute_tag_info_class = 9

    access_mode = flags & os.O_ACCMODE
    if access_mode == os.O_RDWR:
        desired_access = generic_read | generic_write
    elif access_mode == os.O_WRONLY:
        desired_access = generic_write
    else:
        desired_access = generic_read
    desired_access |= file_read_attributes

    if flags & os.O_CREAT and flags & os.O_EXCL:
        creation_disposition = create_new
    elif flags & os.O_CREAT:
        creation_disposition = open_always
    else:
        creation_disposition = open_existing

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
    except (AttributeError, OSError) as error:
        raise OSError(
            errno.ENOTSUP,
            "secure Windows file opening is unavailable",
            path_value,
        ) from error

    handle = kernel32.CreateFileW(
        path_value,
        desired_access,
        file_share_read | file_share_write | file_share_delete,
        None,
        creation_disposition,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    invalid_handle_value = ctypes.c_void_p(-1).value
    if handle == invalid_handle_value:
        windows_error = ctypes.WinError(ctypes.get_last_error())
        windows_error.filename = path_value
        raise windows_error

    handle_is_owned = True
    try:
        file_info = FileAttributeTagInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            file_attribute_tag_info_class,
            ctypes.byref(file_info),
            ctypes.sizeof(file_info),
        ):
            windows_error = ctypes.WinError(ctypes.get_last_error())
            windows_error.filename = path_value
            raise windows_error
        if file_info.file_attributes & file_attribute_reparse_point:
            raise OSError(
                errno.ELOOP,
                "reparse point rejected",
                path_value,
            )

        crt_flags = access_mode
        crt_flags |= getattr(os, "O_BINARY", 0)
        crt_flags |= getattr(os, "O_NOINHERIT", 0)
        handle_value = handle if isinstance(handle, int) else handle.value
        file_descriptor = msvcrt.open_osfhandle(handle_value, crt_flags)
        handle_is_owned = False
        return file_descriptor
    finally:
        if handle_is_owned:
            kernel32.CloseHandle(handle)


def open_file_no_follow(
    path_value: str,
    flags: int,
    *,
    runtime_system: RuntimeSystem,
    mode: int = 0o666,
) -> int:
    if flags & os.O_TRUNC:
        raise ValueError("truncate only after validating the opened file")

    if runtime_system == "windows":
        return _open_windows_file_no_follow(path_value, flags)

    no_follow_flag = getattr(os, "O_NOFOLLOW", None)
    if no_follow_flag is None:
        raise OSError(
            errno.ENOTSUP,
            "secure no-follow file opening is unavailable",
            path_value,
        )
    return os.open(path_value, flags | no_follow_flag, mode)
