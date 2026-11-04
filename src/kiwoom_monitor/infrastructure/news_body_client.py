"""Public BODY job/input/result port; no local DB, provider keys or retry loop."""
from __future__ import annotations

from math import isfinite
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION, NewsBodyDeliveryError, stable_document_hash
from .central_content_client import CentralContentClient, CentralContentHttpError, validate_producer_connection


class NewsBodyClient:
    def __init__(self, server_url: str, owner: str, access_token: str, *, opener=None):
        self.owner=owner
        url=validate_producer_connection(server_url,owner,access_token)
        self.client=CentralContentClient(url,access_token,**({'opener':opener} if opener is not None else {}))

    def server_now(self):
        return self.client.server_now()

    def _request(self, function, *args, **kwargs):
        try:
            return function(*args,**kwargs,expected_owner=self.owner)
        except (OSError,RuntimeError,ValueError,TypeError,KeyError) as error:
            raise NewsBodyDeliveryError(type(error).__name__) from error

    def claim_news_jobs(self, **_ignored_local_claim_options):
        response=self._request(self.client.claim_news_body_job)
        job=response.get('job')
        if job is None:
            return []
        self._job(job)
        return [job]

    def _job(self, job):
        if (not isinstance(job,dict) or job.get('stage')!='BODY' or job.get('owner')!=self.owner
                or job.get('processing_version')!=ARTICLE_BODY_EXTRACTOR_VERSION
                or type(job.get('attempts')) is not int or job['attempts']<1
                or not all(isinstance(job.get(key),str) and len(job[key])==64 and all(c in '0123456789abcdef' for c in job[key]) for key in ('job_key','input_hash'))
                or not isinstance(job.get('article_revision_id'),str) or not job['article_revision_id']
                or not isinstance(job.get('payload'),dict)):
            raise NewsBodyDeliveryError('invalid server BODY claim')

    def load_body_input(self, job):
        self._job(job)
        value=self._request(self.client.load_news_body_input,job['job_key'],job['attempts'])
        if any(value.get(key)!=job[key] for key in ('job_key','attempts','owner','article_revision_id','input_hash','processing_version')):
            raise NewsBodyDeliveryError('BODY input authority differs from claim')
        if value.get('payload')!=job['payload'] or not isinstance(value.get('input_descriptor'),str):
            raise NewsBodyDeliveryError('BODY source or URL input differs from claim')
        job['input_descriptor']=value['input_descriptor']
        body=value.get('cached_body')
        if body is not None and (not isinstance(body,dict) or body.get('article_revision_id')!=job['article_revision_id']):
            raise NewsBodyDeliveryError('cached BODY article differs from claim')
        return body

    def _receipt(self, job, receipt):
        if (not isinstance(receipt,dict) or type(receipt.get('attempts')) is not int
                or any(receipt.get(key)!=job[key] for key in ('job_key','attempts','owner'))
                or receipt.get('state') not in {'completed','failed','pending','already_completed','already_failed'}
                or not isinstance(receipt.get('output_ref'),str)):
            raise NewsBodyDeliveryError('BODY acceptance receipt is unknown')
        return receipt

    def accept_body_result(self, job, result):
        self._job(job)
        envelope={key:job[key] for key in ('attempts','input_hash','processing_version','input_descriptor','payload')}
        envelope['result']=result
        expected_hash=stable_document_hash({'owner':self.owner,'job_key':job['job_key'],'envelope':envelope})
        try:
            receipt=self.client.accept_news_body_result(job['job_key'],envelope,expected_owner=self.owner)
            return self._receipt(job,receipt)
        except CentralContentHttpError as error:
            # A definite rejection must never be disguised by a prior row.
            raise NewsBodyDeliveryError(type(error).__name__) from error
        except (OSError,RuntimeError,ValueError,TypeError,KeyError) as error:
            try:
                receipt=self.client.load_news_body_receipt(job['job_key'],job['attempts'],expected_owner=self.owner)
                self._receipt(job,receipt)
                first=receipt.get('accepted_receipt')
                if (receipt.get('accepted_envelope_hash')!=expected_hash or not isinstance(first,dict)
                        or first.get('state') not in {'completed','failed','pending'}
                        or any(first.get(field)!=receipt[field] for field in ('state','output_ref'))):
                    raise NewsBodyDeliveryError('BODY delivery remains unknown')
                return self._receipt(job,first)
            except (OSError,RuntimeError,ValueError,TypeError,KeyError) as confirm:
                raise NewsBodyDeliveryError('BODY delivery and receipt are unknown') from confirm
