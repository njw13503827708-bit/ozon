"""Fixed FBS/rFBS v4 read contract from the supplied Seller Swagger."""
from datetime import datetime, time, timedelta, timezone

from backend.integrations.ozon.analytics_client import validate_dates
from backend.integrations.ozon.base_client import OzonBaseClient


class OzonOrderClient(OzonBaseClient):
    ENDPOINT = '/v4/posting/fbs/list'

    def list_postings(self, date_from, date_to, *, cursor='', limit=100):
        validate_dates(date_from, date_to)
        if not isinstance(cursor, str) or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('Invalid FBS cursor or page size')
        offset = timezone(timedelta(hours=3))
        start = datetime.combine(datetime.fromisoformat(date_from).date(), time.min, offset)
        end = datetime.combine(datetime.fromisoformat(date_to).date(), time.max, offset)
        return self._post_json(self.ENDPOINT, {'cursor': cursor, 'limit': limit, 'sort_dir': 'ASC',
            'filter': {'since': start.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
                       'to': end.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')},
            'with': {'analytics_data': False, 'financial_data': False, 'barcodes': False, 'legal_info': False}})
