# Copyright (C) 2019 OpenIO SAS, as part of OpenIO SDS
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3.0 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library.

from oio.blob.client import BlobClient
from oio.common.easy_value import float_value, int_value
from oio.common.exceptions import ContentNotFound, OrphanChunk
from oio.conscience.client import ConscienceClient
from oio.content.factory import ContentFactory
from oio.rdir.client import RdirClient
from oio.xcute.common.job import XcuteJob, XcuteTask


class RawxDecommissionTask(XcuteTask):

    def __init__(self, conf, job_params, logger=None):
        super(RawxDecommissionTask, self).__init__(
            conf, job_params, logger=logger)

        self.service_id = job_params['service_id']
        self.rawx_timeout = job_params['rawx_timeout']
        self.min_chunk_size = job_params['min_chunk_size']
        self.max_chunk_size = job_params['max_chunk_size']
        self.excluded_rawx = job_params['excluded_rawx']

        self.blob_client = BlobClient(
            self.conf, logger=self.logger)
        self.content_factory = ContentFactory(self.conf)
        self.conscience_client = ConscienceClient(
            self.conf, logger=self.logger)

        self.fake_excluded_chunks = self._generate_fake_excluded_chunks(
            self.excluded_rawx)

    def _generate_fake_excluded_chunks(self, excluded_rawx):
        fake_excluded_chunks = list()
        fake_chunk_id = '0'*64
        for service_id in excluded_rawx:
            service_addr = self.conscience_client.resolve_service_id(
                'rawx', service_id)
            chunk = dict()
            chunk['hash'] = '0000000000000000000000000000000000'
            chunk['pos'] = '0'
            chunk['size'] = 1
            chunk['score'] = 1
            chunk['url'] = 'http://{}/{}'.format(service_id, fake_chunk_id)
            chunk['real_url'] = 'http://{}/{}'.format(service_addr,
                                                      fake_chunk_id)
            fake_excluded_chunks.append(chunk)
        return fake_excluded_chunks

    def process(self, chunk_id, task_payload, reqid=None):
        chunk_url = 'http://{}/{}'.format(self.service_id, chunk_id)
        meta = self.blob_client.chunk_head(
            chunk_url, timeout=self.rawx_timeout, reqid=reqid)
        container_id = meta['container_id']
        content_id = meta['content_id']
        chunk_size = int(meta['chunk_size'])

        # Maybe skip the chunk because it doesn't match the size constaint
        if chunk_size < self.min_chunk_size:
            self.logger.debug(
                '[reqid=%s] SKIP %s too small', reqid, chunk_url)
            return {'skipped_chunks': 1}
        if self.max_chunk_size > 0 and chunk_size > self.max_chunk_size:
            self.logger.debug(
                '[reqid=%s] SKIP %s too big', reqid, chunk_url)
            return {'skipped_chunks': 1}

        # Start moving the chunk
        try:
            content = self.content_factory.get(
                container_id, content_id, reqid=reqid)
            content.move_chunk(
                chunk_id, fake_excluded_chunks=self.fake_excluded_chunks,
                reqid=reqid)
        except (ContentNotFound, OrphanChunk):
            return {'orphan_chunks': 1}

        return {'moved_chunks': 1, 'moved_bytes': chunk_size}


class RawxDecommissionJob(XcuteJob):

    JOB_TYPE = 'rawx-decommission'
    TASK_CLASS = RawxDecommissionTask

    DEFAULT_RDIR_FETCH_LIMIT = 1000
    DEFAULT_RDIR_TIMEOUT = 60.0
    DEFAULT_RAWX_TIMEOUT = 60.0
    DEFAULT_MIN_CHUNK_SIZE = 0
    DEFAULT_MAX_CHUNK_SIZE = 0

    @classmethod
    def sanitize_params(cls, job_params):
        sanitized_job_params, _ = super(
            RawxDecommissionJob, cls).sanitize_params(job_params)

        # specific configuration
        service_id = job_params.get('service_id')
        if not service_id:
            raise ValueError('Missing service ID')
        sanitized_job_params['service_id'] = service_id

        sanitized_job_params['rdir_fetch_limit'] = int_value(
            job_params.get('rdir_fetch_limit'),
            cls.DEFAULT_RDIR_FETCH_LIMIT)

        sanitized_job_params['rdir_timeout'] = float_value(
            job_params.get('rdir_timeout'),
            cls.DEFAULT_RDIR_TIMEOUT)

        sanitized_job_params['rawx_timeout'] = float_value(
            job_params.get('rawx_timeout'),
            cls.DEFAULT_RAWX_TIMEOUT)

        sanitized_job_params['min_chunk_size'] = int_value(
            job_params.get('min_chunk_size'),
            cls.DEFAULT_MIN_CHUNK_SIZE)

        sanitized_job_params['max_chunk_size'] = int_value(
            job_params.get('max_chunk_size'),
            cls.DEFAULT_MAX_CHUNK_SIZE)

        excluded_rawx = job_params.get('excluded_rawx')
        if excluded_rawx:
            excluded_rawx = excluded_rawx.split(',')
        else:
            excluded_rawx = list()
        sanitized_job_params['excluded_rawx'] = excluded_rawx

        return sanitized_job_params, 'rawx/%s' % service_id

    def get_tasks(self, job_params, marker=None):
        chunk_infos = self.get_chunk_infos(job_params, marker)

        for _, _, chunk_id, _ in chunk_infos:
            yield chunk_id, dict()

    def get_total_tasks(self, job_params, marker=None):
        chunk_infos = self.get_chunk_infos(job_params, marker)

        chunk_id = ''
        i = 0
        for i, (_, _, chunk_id, _) in enumerate(chunk_infos, 1):
            if i % 1000 == 0:
                yield (chunk_id, 1000)

        yield (chunk_id, i % 1000)

    def get_chunk_infos(self, params, marker):
        rdir_client = RdirClient(self.conf, logger=self.logger)

        service_id = params['service_id']
        rdir_fetch_limit = params['rdir_fetch_limit']
        rdir_timeout = params['rdir_timeout']

        chunk_infos = rdir_client.chunk_fetch(
            service_id, timeout=rdir_timeout,
            limit=rdir_fetch_limit, start_after=marker)

        return chunk_infos
