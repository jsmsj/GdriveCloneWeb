import json
import logging
import os

import re

import urllib.parse as urlparse

from urllib.parse import parse_qs
import time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import requests
from google.oauth2 import service_account

from tenacity import *


from loggerfile import logger
from config import app_url

logging.getLogger("googleapiclient.discovery").setLevel(logging.ERROR)


import time
from math import ceil


def find_sa_info_from_index(i):
    with open(f'accounts/{i}.json','r') as f:
        sa_info = json.load(f)
    return sa_info

def humanbytes(size: int) -> str:
    if not size:
        return ""
    power = 2**10
    number = 0
    dict_power_n = {0: " ", 1: "K", 2: "M", 3: "G", 4: "T", 5: "P"}
    while size > power:
        size /= power
        number += 1
    return str(round(size, 3)) + " " + dict_power_n[number] + "B"


def humantime(seconds):
    if seconds > 3600:
        return time.strftime("%Hh%Mm%Ss", time.gmtime(seconds))
    else:
        return time.strftime("%Mm%Ss", time.gmtime(seconds))


def list_into_n_parts(lst, n):
    size = ceil(len(lst) / n)
    return list(map(lambda x: lst[x * size : x * size + size], list(range(n))))


def status_emb(clone_id, **kwargs):
    url = f"{app_url}/progresscheck"
    data = {"clone_id": clone_id}
    data.update(kwargs)
    r = requests.post(url, json=data)


def all_sas():
    return os.listdir("accounts/")


class GoogleDrive:
    def __init__(self, source_url, dest_url, creds, clone_id, use_sa):
        self.__G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
        self.__G_DRIVE_BASE_DOWNLOAD_URL = (
            "https://drive.google.com/uc?id={}&export=download"
        )
        self.__G_DRIVE_DIR_BASE_DOWNLOAD_URL = (
            "https://drive.google.com/drive/folders/{}"
        )
        self.source_url = source_url
        self.dest_url = dest_url
        self.__parent_id = self.getIdFromUrl(dest_url)
        self.size_service = None
        self.clone_id = clone_id
        self.use_sa = use_sa
        self.creds = creds
        self.sa_index = 0
        self.__OAUTH_SCOPE = ['https://www.googleapis.com/auth/drive']
        self.__service = self.authorize()

    def getIdFromUrl(self, link: str):
        if "folders" in link or "file" in link:
            regex = r"https://drive\.google\.com/(drive)?/?u?/?\d?/?(mobile)?/?(file)?(folders)?/?d?/([-\w]+)[?+]?/?(w+)?"
            res = re.search(regex, link)
            if res is None:
                raise IndexError("GDrive ID not found.")
            return res.group(5)
        parsed = urlparse.urlparse(link)
        return parse_qs(parsed.query)["id"][0]

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=6),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(HttpError),
        before=before_log(logger, logging.DEBUG),
    )
    def getFilesByFolderId(self, folder_id):
        page_token = None
        q = f"'{folder_id}' in parents"
        files = []
        while True:
            response = (
                self.__service.files()
                .list(
                    supportsTeamDrives=True,
                    includeTeamDriveItems=True,
                    q=q,
                    spaces="drive",
                    pageSize=200,
                    fields="nextPageToken, files(id, name, mimeType,size)",
                    pageToken=page_token,
                )
                .execute()
            )
            for file in response.get("files", []):
                files.append(file)
            page_token = response.get("nextPageToken", None)
            if page_token is None:
                break
        return files

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=6),
        stop=stop_after_attempt(15),
        retry=retry_if_exception_type(HttpError),
        before=before_log(logger, logging.DEBUG),
    )
    def copyFile(self, file_id, dest_id):
        body = {"parents": [dest_id], "description": "Uploaded by GdriveCloneWeb"}
        try:
            res = (
                self.__service.files()
                .copy(supportsAllDrives=True, fileId=file_id, body=body)
                .execute()
            )
            return res
        except HttpError as err:
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = (
                    json.loads(err.content).get("error").get("errors")[0].get("reason")
                )
                if reason == "userRateLimitExceeded" or reason == "dailyLimitExceeded":
                    if self.use_sa:
                        self.switchSaIndex()
                        return self.copyFile(file_id, dest_id)
                    else:
                        status_emb(self.clone_id, error=reason)

                        logger.debug(reason)
                        raise IndexError(reason)
                else:
                    logger.error(err, exc_info=True)
                    raise err
            else:
                logger.error(err, exc_info=True)

    def cloneFolder(
        self, name, local_path, folder_id, parent_id, total_size: int, total_files
    ):
        files = self.getFilesByFolderId(folder_id)
        new_id = None
        if len(files) == 0:
            return self.__parent_id
        for file in files:
            if file.get("mimeType") == self.__G_DRIVE_DIR_MIME_TYPE:
                file_path = os.path.join(local_path, file.get("name"))
                current_dir_id = self.create_directory(
                    file.get("name"), parent_id=parent_id
                )
                new_id = self.cloneFolder(
                    file.get("name"),
                    file_path,
                    file.get("id"),
                    current_dir_id,
                    total_size,
                    total_files,
                )
            else:
                try:
                    self.transferred_size += int(file.get("size"))
                    self.num_of_files_transferred += 1
                except TypeError:
                    pass
                try:
                    self.copyFile(file.get("id"), parent_id)
                    status_emb(
                        clone_id=self.clone_id,
                        finaldata=False,
                        transferred=humanbytes(self.transferred_size),
                        transferred_b=self.transferred_size,
                        total_size_b=total_size,
                        current_file_name=file.get("name"),
                        current_file_size=humanbytes(int(file.get("size"))),
                        total_size=humanbytes(total_size),
                        total_files=total_files,
                        num_of_files_transferred=self.num_of_files_transferred,
                        speed=f"{humanbytes(int(self.transferred_size/(time.time()-self.start_time)))}/s",
                        eta=humantime(
                            (total_size - self.transferred_size)
                            / int(
                                self.transferred_size / (time.time() - self.start_time)
                            )
                        ),
                        sa_status = str(self.sa_index) if self.use_sa else None
                    )
                    new_id = parent_id
                except Exception as err:
                    status_emb(self.clone_id, error=err)
                    logger.error(err, exc_info=True)
                    return err
        return new_id

    @retry(
        wait=wait_exponential(multiplier=2, min=3, max=6),
        stop=stop_after_attempt(5),
        retry=retry_if_exception_type(HttpError),
        before=before_log(logger, logging.DEBUG),
    )
    def create_directory(self, directory_name, **kwargs):
        if not kwargs == {}:
            parent_id = kwargs.get("parent_id")
        else:
            parent_id = self.__parent_id
        file_metadata = {
            "name": directory_name,
            "mimeType": self.__G_DRIVE_DIR_MIME_TYPE,
            "description": "Uploaded by GdriveCloneWeb",
        }
        file_metadata["parents"] = [parent_id]
        file = (
            self.__service.files()
            .create(supportsTeamDrives=True, body=file_metadata)
            .execute()
        )
        file_id = file.get("id")
        return file_id

    def clone(self):
        self.transferred_size = 0
        self.num_of_files_transferred = 0
        self.start_time = time.time()
        file_id = self.getIdFromUrl(self.source_url)

        try:
            self.size_service = TotalSize(file_id, self.__service)
            total_size, total_files = self.size_service.calc_size_and_files()
            meta = (
                self.__service.files()
                .get(
                    supportsAllDrives=True,
                    fileId=file_id,
                    fields="name,id,mimeType,size",
                )
                .execute()
            )
            if meta.get("mimeType") == self.__G_DRIVE_DIR_MIME_TYPE:
                dir_id = self.create_directory(
                    meta.get("name"), parent_id=self.__parent_id
                )
                result = self.cloneFolder(
                    meta.get("name"),
                    meta.get("name"),
                    meta.get("id"),
                    dir_id,
                    total_size,
                    total_files,
                )
                finaldata = {
                    "name": meta.get("name"),
                    "down_url": self.__G_DRIVE_DIR_BASE_DOWNLOAD_URL.format(dir_id),
                    "total_size": humanbytes(self.transferred_size),
                    "transferred_files": self.num_of_files_transferred,
                    "total_files": total_files,
                    "speed": f"{humanbytes(int(self.transferred_size/(time.time()-self.start_time)))}/s",
                    "elapsed_time": humantime(time.time() - self.start_time),
                }
                status_emb(clone_id=self.clone_id, finaldata=True, **finaldata)
            else:
                file = self.copyFile(meta.get("id"), self.__parent_id)
                self.num_of_files_transferred += 1
                finaldata = {
                    "name": file.get("name"),
                    "down_url": self.__G_DRIVE_BASE_DOWNLOAD_URL.format(file.get("id")),
                    "total_size": humanbytes(int(meta.get("size"))),
                    "transferred_files": self.num_of_files_transferred,
                    "total_files": total_files,
                    "speed": f"{humanbytes(int(int(meta.get('size'))/(time.time()-self.start_time)))}/s",
                    "elapsed_time": humantime(time.time() - self.start_time),
                }
                status_emb(clone_id=self.clone_id, finaldata=True, **finaldata)
        except Exception as err:
            if isinstance(err, RetryError):
                err = err.last_attempt.exception()
            err = str(err).replace(">", "").replace("<", "")
            print(err)
            status_emb(self.clone_id, error=err)

    def switchSaIndex(self):
        all_sas = all_sas()
        if self.sa_index == len(all_sas) - 1:
            self.sa_index = 0
        print(f"Switching sas from {self.sa_index} to {self.sa_index+1}")
        self.sa_index += 1
        self.__service = self.authorize()

    def authorize(self):
        if not self.use_sa:
            creds = self.creds
        else:
            sa_info = find_sa_info_from_index(self.sa_index)
            sa = {
                "client_email":sa_info["client_email"],
                "token_uri":sa_info["token_uri"],
                "private_key":sa_info["private_key"]
            }
            creds = service_account.Credentials.from_service_account_info(sa,scopes=self.__OAUTH_SCOPE)
        return build("drive", "v3", credentials=creds, cache_discovery=False)


class TotalSize:
    def __init__(self, gdrive_id, service):
        self.link_id = gdrive_id
        self.__G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
        self.__service = service
        self.total_bytes = 0
        self.total_files = 0

    def calc_size_and_files(self):
        drive_file = (
            self.__service.files()
            .get(
                fileId=self.link_id,
                fields="id, mimeType, size",
                supportsTeamDrives=True,
            )
            .execute()
        )
        if drive_file["mimeType"] == self.__G_DRIVE_DIR_MIME_TYPE:
            self.gDrive_directory(**drive_file)
        else:
            self.gDrive_file(**drive_file)
        return self.total_bytes, self.total_files

    def list_drive_dir(self, file_id: str) -> list:
        query = f"'{file_id}' in parents and (name contains '*')"
        fields = "nextPageToken, files(id, mimeType, size)"
        page_token = None
        page_size = 1000
        files = []
        while True:
            response = (
                self.__service.files()
                .list(
                    supportsTeamDrives=True,
                    includeTeamDriveItems=True,
                    q=query,
                    spaces="drive",
                    fields=fields,
                    pageToken=page_token,
                    pageSize=page_size,
                    corpora="allDrives",
                    orderBy="folder, name",
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken", None)
            if page_token is None:
                break
        return files

    def gDrive_file(self, **kwargs):
        try:
            size = int(kwargs["size"])
            self.total_files += 1
        except:
            size = 0
        self.total_bytes += size

    def gDrive_directory(self, **kwargs) -> None:
        files = self.list_drive_dir(kwargs["id"])
        if len(files) == 0:
            return
        for file_ in files:
            if file_["mimeType"] == self.__G_DRIVE_DIR_MIME_TYPE:
                self.gDrive_directory(**file_)
            else:
                self.gDrive_file(**file_)
