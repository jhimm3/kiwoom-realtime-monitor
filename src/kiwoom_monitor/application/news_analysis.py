from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NewsAssessment:
    relevant: bool
    category: str
    outlook: str
    reason: str
    relevance_score: int
    outlook_score: int


SECURITIES_TERMS = {
    "주가": 2, "증시": 2, "코스피": 2, "코스닥": 2, "상한가": 3,
    "하한가": 3, "급등": 2, "급락": 2, "거래량": 2, "공시": 3,
    "실적": 3, "매출": 2, "영업이익": 3, "영업익": 3, "순이익": 2, "적자": 2,
    "흑자": 2, "수주": 3, "공급계약": 3, "계약": 1, "투자": 2,
    "인수": 2, "합병": 2, "증자": 3, "자본감소": 3, "무상감자": 3, "유상감자": 3, "감자 결정": 3, "전환사채": 3,
    "자사주": 3, "배당": 3, "임상": 2, "허가": 2, "특허": 2,
    "최대주주": 3, "목표주가": 3, "투자의견": 3, "증권사": 2,
    "상장": 2, "거래정지": 3, "기업가치": 2, "시가총액": 2,
    "소송": 3, "리콜": 3, "제재": 3, "상장폐지": 4,
    "횡령": 4, "배임": 4, "압수수색": 4, "사업 참여": 2,
}

CATEGORY_TERMS = (
    ("실적·전망", (
        "실적", "매출", "영업이익", "영업익", "순이익", "흑자", "적자",
        "실적 전망", "이익 전망", "전망치", "추정치", "컨센서스", "가이던스",
    )),
    ("수주·계약", ("수주", "공급계약", "공급", "납품", "계약 체결")),
    ("투자·인수합병", (
        "투자 결정", "시설투자", "설비투자", "인수", "합병", "m&a", "지분 취득",
    )),
    ("자본·주주환원", ("유상증자", "무상증자", "자본감소", "무상감자", "유상감자", "감자 결정", "전환사채", "자사주", "배당")),
    ("임상·허가", ("임상", "허가", "승인", "특허", "신약")),
    ("경영권·주주", ("최대주주", "경영권", "대표이사", "주주총회")),
    ("주가·수급", ("주가", "상한가", "하한가", "급등", "급락", "거래량", "수급")),
    ("공시·규제", ("공시", "거래정지", "불성실공시", "소송", "리콜", "제재", "조사", "규제")),
)

POSITIVE_TERMS = {
    "수주": 3, "공급계약": 3, "계약 체결": 2, "흑자전환": 4, "흑자 전환": 4,
    "사상 최대": 3, "최대 실적": 3, "급증": 2, "증가": 1, "성장": 1,
    "상향": 2, "승인": 3, "허가": 2, "특허": 2, "임상 성공": 4,
    "자사주 취득": 3, "자사주 소각": 4, "배당 확대": 3, "신고가": 2,
    "상한가": 2, "턴어라운드": 3, "국책과제": 2, "반등 흐름": 3,
    "반등세": 3, "회복세": 3, "상승 전환": 3,
    "실적 개선": 2, "우선협상대상자 선정": 3, "기술이전 계약": 3,
    "전량 계약 마감": 3, "생산능력 확대": 2, "상용화·양산": 2,
    "수혜": 2, "호재": 2, "긍정적": 2, "겹호재": 3, "재평가": 2,
    "목표가 상향": 3, "매수 의견": 2,
}

NEGATIVE_TERMS = {
    "적자전환": 4, "적자 전환": 4, "영업손실": 3, "순손실": 3,
    "급감": 2, "감소": 1, "하향": 2, "유상증자": 3, "자본감소": 3,
    "무상감자": 3, "유상감자": 3, "감자 결정": 3,
    "전환사채": 1, "거래정지": 4, "상장폐지": 5, "횡령": 5, "배임": 5,
    "압수수색": 4, "소송": 2, "제재": 3, "리콜": 3, "임상 실패": 5,
    "허가 취소": 4, "계약 해지": 4, "수주 취소": 4, "하한가": 3,
    "실적 악화": 3, "첫 적자": 3, "적자 부담": 4,
    "투자손실": 3, "수익성 악화": 3, "재무 부담": 3,
    "경고등": 2, "계약 지연·위험": 3,
}

_POSITIVE_DIRECTION_PATTERNS = (
    ("자사주 소각", re.compile(r"자사주[^.!?]{0,24}(?:전량\s*)?소각", re.IGNORECASE)),
    ("실적 개선", re.compile(
        r"(?:매출|영업이익|영업익|순이익|순익)[^.!?]{0,28}(?:증가|급증|개선|흑자)",
        re.IGNORECASE,
    )),
    ("우선협상대상자 선정", re.compile(r"우선협상대상자[^.!?]{0,12}선정", re.IGNORECASE)),
    ("기술이전 계약", re.compile(r"(?:기술이전|라이선스(?:\s*아웃)?)[^.!?]{0,24}계약", re.IGNORECASE)),
    ("전량 계약 마감", re.compile(r"(?:전량|전부|모두)[^.!?]{0,18}계약[^.!?]{0,12}(?:마감|완료)", re.IGNORECASE)),
    ("생산능력 확대", re.compile(
        r"(?:생산능력|생산량|수주잔고)[^.!?]{0,25}(?:확대(?!될수록)|증가|늘|\d+(?:\.\d+)?\s*배)",
        re.IGNORECASE,
    )),
    ("상용화·양산", re.compile(r"(?:상용화|양산)[^.!?]{0,20}(?:돌입|개시|시작|계획)", re.IGNORECASE)),
    ("목표가 상향", re.compile(
        r"목표(?:주가|가)[^.!?]{0,18}(?:상향|올려|높여|↑)", re.IGNORECASE,
    )),
    ("매수 의견", re.compile(
        r"(?:투자의견[^.!?]{0,16}매수|매수(?:\([^)]*\))?[^.!?]{0,16}(?:유지|제시))", re.IGNORECASE,
    )),
)

_NEGATIVE_DIRECTION_PATTERNS = (
    ("실적 악화", re.compile(
        r"(?:(?:매출|영업이익|영업익|순이익|순익)[^.!?]{0,24}(?:감소|급감|악화|적자\s*전환)|"
        r"(?:영업손실|순손실)[^.!?]{0,24}(?:증가|급증|확대|지속))",
        re.IGNORECASE,
    )),
    ("계약 지연·위험", re.compile(
        r"(?:계약|수주)[^.!?]{0,22}(?:연기|지연|차질|무산|돌발변수)", re.IGNORECASE,
    )),
    ("첫 적자", re.compile(r"(?:사상\s*)?첫\s*적자", re.IGNORECASE)),
    ("적자 부담", re.compile(
        r"(?:적자\s*자회사|적자[^.!?]{0,14}(?:지속|확대|누적|떠안|부담))", re.IGNORECASE,
    )),
)

_BODY_POSITIVE_EVIDENCE = {
    "수주", "공급계약", "계약 체결", "흑자전환", "흑자 전환", "사상 최대",
    "최대 실적", "승인", "허가", "특허", "임상 성공", "자사주 취득",
    "자사주 소각", "배당 확대", "턴어라운드", "국책과제", "실적 개선",
    "우선협상대상자 선정", "기술이전 계약", "전량 계약 마감",
    "생산능력 확대", "상용화·양산", "목표가 상향", "매수 의견",
}
_BODY_NEGATIVE_EVIDENCE = {
    "적자전환", "적자 전환", "영업손실", "순손실", "유상증자", "자본감소",
    "무상감자", "유상감자", "감자 결정", "거래정지", "상장폐지", "횡령",
    "배임", "압수수색", "제재", "리콜", "임상 실패", "허가 취소",
    "계약 해지", "수주 취소", "실적 악화", "첫 적자", "적자 부담",
    "투자손실", "수익성 악화", "재무 부담", "경고등", "계약 지연·위험",
}

_LATE_BODY_SCAN_CHARS = 2_000
_LATE_BODY_STALE = re.compile(
    r"지난달|지난해|작년|과거|당시|앞서|종전|기존|이미|"
    r"(?:올해|금년|지난)\s*(?:상반기|하반기|\d+분기|\d{1,2}월)|"
    r"\d{4}년|\d+\s*(?:일|개월|년)\s*전",
    re.IGNORECASE,
)
_LATE_BODY_UNCERTAIN = re.compile(
    r"유상증자[^.!?]{0,18}(?:제외|하지|아니)|배당\s*확대[^.!?]{0,18}(?:어렵|곤란)|"
    r"(?:수주|계약)[^.!?]{0,24}(?:기대|예상|전망|계획|목표|방침|가능성|나설|나서|"
    r"연결되는지|대응할|추진|확정된\s*사안은\s*아니)|수주전|수주\s*단계|"
    r"목표(?:주가|가)[^.!?]{0,18}유지",
    re.IGNORECASE,
)
_LATE_BODY_ASSERTED_EVENT = re.compile(
    r"(?:공급\s*계약|계약|수주)[^.!?]{0,32}(?:체결|맺|확정|성사|공시|완료|성공)|"
    r"(?:체결|맺|확정|성사|공시|완료|성공)[^.!?]{0,32}(?:공급\s*계약|계약|수주)|"
    r"(?:흑자|적자)\s*전환[^.!?]{0,24}(?:성공|기록|했다|됐다)|"
    r"(?:매출|영업이익|영업익|순이익|순익)[^.!?]{0,35}(?:증가|급증|감소|급감|개선|악화)|"
    r"(?:유상증자|무상증자|감자|자사주\s*(?:취득|소각)|배당\s*(?:확대|축소))"
    r"[^.!?]{0,30}(?:결정|공시|단행|추진|완료)|"
    r"(?:목표주가|목표가|투자의견)[^.!?]{0,24}(?:상향|하향|매수|매도)|"
    r"(?:승인|허가|특허)[^.!?]{0,25}(?:받|획득|취득|등록|취소|거절)|"
    r"(?:거래정지|상장폐지|횡령|배임|압수수색|피소|소송\s*제기|리콜|제재)|"
    r"(?:계약\s*해지|수주\s*취소|영업손실|순손실|임상\s*(?:성공|실패))",
    re.IGNORECASE,
)

_PRICE_REACTION_TITLE = re.compile(
    r"(?:\[(?:핫종목|핫스탁)[^]]*\]|"
    r"\[(?:특징주|장중시황|마감시황|시황)[^]]*\]"
    r"[^\n]{0,100}(?:급등|급락|강세|약세|상한가|하한가|상승|하락|반등|반락|폭등|폭락|↑|↓)|"
    r"(?:코스피|코스닥|증시|지수선물|선물옵션|프리마켓|애프터마켓)"
    r"[^\n]{0,100}(?:급등|급락|강세|약세|상한가|하한가|상승|하락|반등|폭등|폭락|돌파|탈환|회복|붕괴|밀려|반납|되찾|마감|↑|↓)|"
    r"(?:장중|오전|오후|마감|주가|\d[\d,]*(?:조|억|만)?원(?:대(?:로)?)?|\d[\d,]*선)"
    r"[^\n]{0,80}(?:급등|급락|강세|약세|상한가|하한가|상승|하락|반등|보합|돌파|탈환|회복|붕괴|밀려|터치|치솟|뛴|↑|↓)|"
    r"(?:급등|급락|강세|약세|상한가|하한가|상승세|하락세|폭등|폭락|반락))",
    re.IGNORECASE,
)

_MARKET_SUMMARY_TITLE = re.compile(
    r"(?:^\[코스피\s*[·&/]\s*코스닥(?:,|\s)|"
    r"\[(?:장중시황|마감시황|주식마감|시황|오늘증시|뉴욕증시\s*브리핑|국내증시|"
    r"코스피\s*[·&/]\s*코스닥[^]]*|"
    r"마켓\s*프리뷰|[^]]*증시\s*풍향계|오늘의\s*삼전닉스)[^]]*\]|"
    r"(?:코스피|코스닥|증시|지수선물|선물옵션)[^\n]{0,100}"
    r"(?:급등|급락|강세|약세|상승|하락|반등|반락|돌파|탈환|회복|붕괴|밀려|"
    r"반납|후퇴|등락|출발|개장|마감|팔자|사자|안정세|뚝|활짝|↑|↓)|"
    r"(?:삼전닉스|반도체주|원전주)[^\n]{0,80}"
    r"(?:급등|급락|강세|약세|상승|하락|반등|반락|팔아치운|엑소더스|증발|↑|↓)|"
    r"(?:베스트\s*&\s*워스트|급등락주\s*짚어보기|서울데이터랩|증시\s*풍향계|"
    r"주식\s*혼조\s*마감))",
    re.IGNORECASE,
)

_MARKET_FLOW_SUMMARY_TITLE = re.compile(
    r"(?:\[(?:(?:주간\s*)?(?:코스피|코스닥|거래소)\s*)?(?:외국인|기관|개인)[^]]*\]|"
    r"\[공매도[^]]*\]|"
    r"(?:거래대금|공매도)[^\n]{0,90}(?:상위|순위|전체의|집중|1위)|"
    r"(?:외국인|기관|개인|개미)[^\n]{0,90}(?:순매수|순매도|집중\s*매도|매도\s*폭탄|팔자)|"
    r"(?:시총|시가총액)[^\n]{0,90}(?:증발|축소|감소|증가|늘어|줄어)|"
    r"희비\s*교차|웃고[^\n]{0,45}울었다)",
    re.IGNORECASE,
)

_MATERIAL_BUSINESS_EVENT = re.compile(
    r"(?:제품|솔루션|기술|자체\s*개발|세계\s*최초|국내\s*최초)[^.!?]{0,35}(?:출시|공개|개발|적용)|"
    r"(?:첫|신규)\s*진출|신사업[^.!?]{0,25}(?:진출|출범|공개)|"
    r"(?:수출|판매)[^.!?]{0,25}(?:\d+(?:\.\d+)?\s*(?:%|↑|↓)|급증|증가|감소)|"
    r"\d[\d,.]*(?:억|조)원?[^.!?]{0,30}(?:투입|투자|베팅|공급|납품|증설)|"
    r"(?:공장|설비|생산)[^.!?]{0,30}(?:증설|확대|감축|중단|재개)|"
    r"(?:착공|준공|양산\s*(?:개시|시작)|공급망[^.!?]{0,20}(?:차질|중단))",
    re.IGNORECASE,
)

# 환율·유가·금리·실적 전망의 움직임을 종목 주가 움직임으로 오인하지 않는다.
# 이 표현을 먼저 지운 뒤에도 주가/지수 반응 표현이 남으면 시세 기사로 본다.
_NON_EQUITY_REACTION = re.compile(
    r"(?:환율(?:\s*\d[\d,.]*원대(?:로)?)?|원화|달러|엔화|유가|원유|금리|국채금리|수익률|"
    r"실적\s*전망|영업이익\s*전망|영업익\s*전망|순이익\s*전망|매출\s*전망|전망치|추정치|컨센서스|"
    r"판매량?|수출|점유율|사용량|재이용량|생산량)"
    r"\s*(?:은|는|이|가|도|까지|마저|의)?\s*['\"‘’“”()]*[^,.!?]{0,24}?"
    r"(?:급등|급락|강세|약세|상승|하락|반등|폭등|폭락|뚝|치솟|증발|줄하향|↑|↓)",
    re.IGNORECASE,
)

# 가격 움직임의 원인으로 함께 보도할 가치가 있는, 확인 가능한 새 사건 표현이다.
# 단순한 "기대", "관심", "매수세"는 이미 반영된 시세 설명으로 남겨 둔다.
_SUBSTANTIVE_EVENT = re.compile(
    r"(?:공급\s*계약|계약\s*(?:체결|해지|취소|종료|공시)|수주|납품|"
    r"(?:잠정\s*)?실적\s*(?:발표|공시)|흑자\s*전환|적자\s*전환|(?:매출|영업이익|순이익|영업손실|순손실)"
    r"[^.!?]{0,25}(?:증가|감소|급증|급감|흑자|적자|전환|발표|공시|전망|최대|1위)|"
    r"유상증자|무상증자|자본감소|무상감자|유상감자|감자\s*결정|전환사채|"
    r"자사주\s*(?:취득|소각)|배당\s*(?:결정|확대|축소)|"
    r"인수\s*(?:결정|계약|완료)|합병\s*(?:결정|계약|승인)|지분\s*취득|"
    r"임상[^.!?]{0,20}(?:성공|실패|결과|승인|신청|중단)|허가\s*(?:승인|취소|신청)|"
    r"품목허가|FDA\s*(?:승인|신청|거절|보완요구)|특허\s*(?:취득|등록|소송)|"
    r"최대주주\s*(?:변경|매각)|대표이사\s*(?:선임|사임)|경영권\s*(?:분쟁|인수)|"
    r"거래정지|상장폐지|횡령|배임|압수수색|소송|제재|리콜|"
    r"목표주가|목표가|투자의견|수출(?:\s*계약|\s*성사)?|신제품\s*출시|공장\s*(?:증설|착공|준공)|투자\s*결정|"
    r"(?:개발|사업)\s*참여|"
    r"정부[^.!?]{0,20}(?:발표|확정|시행)|정책[^.!?]{0,15}(?:발표|확정|시행))",
    re.IGNORECASE,
)

_LOW_VALUE_NEWS = re.compile(
    r"(?:골프|야구|축구|배구)[^.!?]{0,35}(?:대회|경기|선수|우승|포토|사진)|"
    r"(?:포토|사진)[^.!?]{0,30}(?:골프|야구|축구|배구)|"
    r"(?:할인|쿠폰|경품)\s*(?:행사|이벤트)|사회공헌|봉사활동|기부금?\s*전달|"
    r"채용\s*(?:설명회|박람회)|시승기|(?:셔틀|통근)\s*버스|"
    r"(?:인문|문화재|지역사회)[^.!?]{0,35}(?:문화공간|공간\s*조성|맞손|협약)|"
    r"(?:복지|셔틀\s*버스)[^.!?]{0,30}(?:집값|아파트)|"
    r"(?:집값|아파트)[^.!?]{0,30}(?:복지|셔틀\s*버스)",
    re.IGNORECASE,
)

_DIRECT_PERCENT_MOVE = re.compile(
    r"\d+(?:\.\d+)?\s*%[^\n]{0,24}(?:급등|급락|강세|약세|상승|하락|내려|치솟|반등|반락|보합|롤러코스터|↑|↓)|"
    r"(?:급등|급락|강세|약세|상승|하락|내려|치솟|반등|반락|보합|롤러코스터)[^\n]{0,24}\d+(?:\.\d+)?\s*%",
    re.IGNORECASE,
)

_HARD_SUMMARY_NOISE = re.compile(
    r"(?:무단\s*전재|재배포\s*금지|Copyright|All\s+rights\s+reserved|저작권자|"
    r"좋아요\s*\d+\s*나빠요\s*\d+|기자의\s*다른\s*기사|이\s*기자의\s*최신글|"
    r"호가시행일|page\s*\d+|자기주식매매\s*신청내역|기사모음|랭킹\s*뉴스|"
    r"많이\s*본\s*뉴스|함께\s*볼만한\s*뉴스|구독하고\s*메인에서|"
    r"정기간행물\s*등록번호|고충처리인|AI\s*학습\s*및\s*활용\s*금지)",
    re.IGNORECASE,
)
_MAX_SUMMARY_SENTENCE_CHARS = 900


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip().casefold()


def _direction_terms(text: str) -> tuple[list[str], list[str]]:
    positive = [term for term in POSITIVE_TERMS if term in text]
    recovery_context = bool(re.search(
        r"(?:급감|급락|감소)(?:했던|했으나|한|세| 이후| 후)?[^.!?]{0,45}(?:반등|회복|상승 전환)", text
    ))
    negative = [
        term for term in NEGATIVE_TERMS
        if term in text and not (recovery_context and term in {"급감", "감소"})
    ]
    for term, pattern in _POSITIVE_DIRECTION_PATTERNS:
        if term not in positive and pattern.search(text):
            positive.append(term)
    for term, pattern in _NEGATIVE_DIRECTION_PATTERNS:
        if term not in negative and pattern.search(text):
            negative.append(term)
    if recovery_context and not any(term in positive for term in ("반등 흐름", "반등세", "회복세", "상승 전환")):
        positive.append("과거 하락 뒤 반등")
    return positive, negative


def _direct_company_match(stock_name: str, sentence: str) -> re.Match[str] | None:
    name = re.sub(r"\s+", r"\\s*", re.escape(stock_name.strip()))
    if not name:
        return None
    suffix = r"(?=$|[\s,()\[\]·]|은|는|이|가|을|를|의|에|에서|으로|로|와|과|도|만|측)"
    return re.search(name + suffix, sentence, re.IGNORECASE)


def _body_direction_terms(stock_name: str, article_body: str) -> tuple[list[str], list[str]]:
    """대상 회사의 확정 사건 문장에서 강한 방향 근거만 고른다."""
    if not stock_name.strip() or not article_body:
        return [], []
    compact_name = re.sub(r"\s+", "", stock_name).casefold()
    # 기존 첫 350자 계약은 그대로 보존하고, 확정 사건 필터는 후반 보완분에만 적용한다.
    direct_sentences = [
        sentence for sentence in re.split(r"[\n.!?]+", article_body[:350])
        if compact_name in re.sub(r"\s+", "", sentence).casefold()
    ]
    for match in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", article_body[:_LATE_BODY_SCAN_CHARS]):
        sentence = match.group().strip()
        if match.start() < 350:
            continue
        company = _direct_company_match(stock_name, sentence)
        if company is None:
            continue
        if _LATE_BODY_STALE.search(sentence) or _LATE_BODY_UNCERTAIN.search(sentence):
            continue
        asserted = _LATE_BODY_ASSERTED_EVENT.search(sentence)
        if asserted is None or company.start() > asserted.start() or asserted.start() - company.end() > 120:
            continue
        direct_sentences.append(sentence)
    if not direct_sentences:
        return [], []
    positive, negative = _direction_terms(" ".join(direct_sentences))
    return (
        [term for term in positive if term in _BODY_POSITIVE_EVIDENCE],
        [term for term in negative if term in _BODY_NEGATIVE_EVIDENCE],
    )


def extractive_news_summary(
    stock_name: str, title: str, article_body: str, *, max_sentences: int = 3,
) -> tuple[str, ...]:
    """원문 문장을 바꾸지 않고 핵심 사건·수치 문장을 골라 원래 순서로 반환한다."""
    body = re.sub(r"\s+", " ", str(article_body or "")).strip()
    if not body:
        return ()
    raw_sentences = [
        value.strip()
        for value in re.findall(r".+?(?:[.!?](?=\s|$)|$)", body)
        if value.strip()
    ]
    sentences: list[str] = []
    seen: set[str] = set()
    for sentence in raw_sentences:
        key = re.sub(r"[^0-9a-z가-힣]+", "", sentence.casefold())
        if len(key) < 6 or key in seen:
            continue
        seen.add(key)
        sentences.append(sentence)
    sentences = [
        sentence for sentence in sentences
        if not _HARD_SUMMARY_NOISE.search(sentence) and len(sentence) <= _MAX_SUMMARY_SENTENCE_CHARS
    ]
    if not sentences:
        return ()
    if len(sentences) <= max(1, max_sentences):
        selected: list[str] = []
        selected_tokens: list[set[str]] = []
        for sentence in sentences:
            tokens = _summary_tokens(sentence)
            if any(_near_duplicate_tokens(tokens, previous) for previous in selected_tokens):
                continue
            selected.append(sentence)
            selected_tokens.append(tokens)
        return tuple(selected)

    title_terms = {
        token.casefold() for token in re.findall(r"[0-9A-Za-z가-힣]+", title)
        if len(token) >= 2 and token not in {"관련", "대한", "통해", "위해", "기자", "종합"}
    }
    normalized_name = re.sub(r"\s+", "", stock_name).casefold()
    title_is_price_story = bool(_PRICE_REACTION_TITLE.search(_plain(title)))
    scored: list[tuple[int, int]] = []
    for index, sentence in enumerate(sentences):
        plain = _plain(sentence)
        score = max(0, 4 - index)
        if normalized_name and normalized_name in re.sub(r"\s+", "", plain):
            score += 7
        score += min(8, sum(weight for term, weight in SECURITIES_TERMS.items() if term in plain))
        if _SUBSTANTIVE_EVENT.search(plain) or _MATERIAL_BUSINESS_EVENT.search(plain):
            score += 8
        if re.search(
            r"(?:생산능력|생산량|매출|영업이익|순이익|수주잔고|점유율)"
            r"[^.!?]{0,30}(?:증가|감소|확대|축소|개선|악화|전환)", plain,
        ):
            score += 6
        if re.search(r"\d[\d,.]*\s*(?:%|원|억원|조원|주|건|배|명)", sentence):
            score += 4
        if re.search(r"(?:발표|공시|결정|체결|승인|취득|전환|증가|감소|확대|중단|재개)(?:했|됐|됐다|한다고)", plain):
            score += 3
        score += min(12, sum(4 for token in title_terms if token in plain))
        if re.search(
            r"(?:기자\s*=|\[사진|사진\s*=|사진\s|자료사진|/공동취재|ⓒ|무단전재|제보는|"
            r"구독|Copyright|좋아요\s*\d+|나빠요\s*\d+|기자의\s*다른기사|호가시행일|page\d+|"
            r"자기주식매매\s*신청내역|(?:관계자|참석자)[^.!?]{0,80}(?:박수를|포즈를|기념촬영))",
            sentence, re.IGNORECASE,
        ):
            score -= 50
        if not title_is_price_story and _PRICE_REACTION_TITLE.search(plain) and not (
            _SUBSTANTIVE_EVENT.search(plain) or _MATERIAL_BUSINESS_EVENT.search(plain)
        ):
            score -= 6
        scored.append((score, index))

    limit = max(1, min(6, int(max_sentences)))
    selected: set[int] = set()
    limited_topics: set[str] = set()
    for _score, index in sorted(scored, key=lambda value: (-value[0], value[1])):
        candidate_tokens = _summary_tokens(sentences[index])
        if any(_near_duplicate_tokens(candidate_tokens, _summary_tokens(sentences[value])) for value in selected):
            continue
        topic = _limited_summary_topic(sentences[index])
        if topic and topic in limited_topics:
            continue
        selected.add(index)
        if topic:
            limited_topics.add(topic)
        if len(selected) >= limit:
            break
    return tuple(sentences[index] for index in sorted(selected))


def _summary_tokens(sentence: str) -> set[str]:
    return {
        token.casefold() for token in re.findall(r"[0-9A-Za-z가-힣]+", sentence)
        if len(token) >= 2
    }


def _near_duplicate_tokens(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    return len(left & right) / min(len(left), len(right)) >= 0.72


def _limited_summary_topic(sentence: str) -> str:
    if re.search(r"(?:목표주가|목표가|투자의견|비중\s*확대|매수\s*의견)", sentence, re.IGNORECASE):
        return "analyst_opinion"
    if sentence.lstrip().startswith("◆"):
        return "section_heading"
    return ""


def is_price_reaction_news(
    stock_name: str, title: str, description: str, article_body: str = "",
) -> bool:
    """새 기업 사건 없이 이미 발생한 가격 움직임만 설명하는 기사인지 판정한다."""
    title_text = _plain(title)
    equity_reaction_text = _NON_EQUITY_REACTION.sub(" ", title_text)
    if _MARKET_SUMMARY_TITLE.search(equity_reaction_text):
        return True
    if _MARKET_FLOW_SUMMARY_TITLE.search(equity_reaction_text):
        # 시총 증감 문구가 있어도 제목에 유상증자·실적 발표처럼 원인이 되는
        # 새 기업 사건이 명시돼 있으면 단순 순위·수급 요약으로 버리지 않는다.
        if _SUBSTANTIVE_EVENT.search(title_text) or _MATERIAL_BUSINESS_EVENT.search(title_text):
            return False
        return True
    name = re.sub(r"\s+", "", stock_name).casefold()
    direct_percent_move = bool(
        name
        and name in re.sub(r"\s+", "", equity_reaction_text)
        and _DIRECT_PERCENT_MOVE.search(equity_reaction_text)
    )
    if not (_PRICE_REACTION_TITLE.search(equity_reaction_text) or direct_percent_move):
        return False
    # 제목에서 새 사건을 직접 말하면 가격 반응 기사라도 보존한다. 검색 요약과
    # 본문 뒤쪽에는 과거 실적·계약을 배경으로 다시 붙이는 경우가 많아, 그 전체를
    # 근거로 삼으면 거의 모든 장중 시세 기사가 다시 관련 뉴스가 된다.
    if _SUBSTANTIVE_EVENT.search(title_text) or _MATERIAL_BUSINESS_EVENT.search(title_text):
        return False
    if name and article_body:
        # 제목에서 빠진 당일 공시가 있을 수 있으므로 원문의 도입부만 보완 근거로
        # 사용한다. 뒤쪽의 기업 소개·지난달 계약은 새 사건으로 승격하지 않는다.
        lead = _plain(article_body)[:700]
        for sentence in re.split(r"[\n.!?]+", lead)[:5]:
            fresh = re.search(r"(?:오늘|금일|이날|당일|발표했다|공시했다|결정했다|체결했다)", sentence)
            stale = re.search(r"(?:지난달|지난해|작년|과거|당시|\d+\s*(?:일|개월|년)\s*전)", sentence)
            if (
                fresh
                and not stale
                and name in re.sub(r"\s+", "", sentence)
                and _SUBSTANTIVE_EVENT.search(sentence)
            ):
                return False
    return True


def assess_stock_news(
    stock_name: str, title: str, description: str, *, article_body: str = "",
) -> NewsAssessment:
    """보수적인 규칙으로 증권 관련성과 호재·악재 *가능성*을 판정한다."""
    name = re.sub(r"\s+", "", stock_name).casefold()
    title_text = _plain(title)
    body_text = _plain(description)
    article_text = _plain(article_body) if article_body else ""
    article_lead = article_text[:700]
    combined = f"{title_text} {body_text}"
    compact = re.sub(r"\s+", "", combined)
    title_compact = re.sub(r"\s+", "", title_text)
    direct_in_title = bool(name and name in title_compact)
    article_lead_compact = re.sub(r"\s+", "", article_lead)
    lead_opening = re.split(r"[.!?]", article_lead, maxsplit=1)[0]
    direct_in_lead_opening = bool(name and name in re.sub(r"\s+", "", lead_opening))
    direct = bool(name and (name in compact or name in article_lead_compact))
    relevance_score = (4 if direct else 0) + sum(weight for term, weight in SECURITIES_TERMS.items() if term in combined)
    direct_event_title = bool(_SUBSTANTIVE_EVENT.search(title_text) or _MATERIAL_BUSINESS_EVENT.search(title_text))
    relevant = (
        direct
        and (direct_in_title or (direct_event_title and direct_in_lead_opening))
        and (relevance_score >= 5 or bool(_MATERIAL_BUSINESS_EVENT.search(combined)))
    )

    category = "기타 증권뉴스"
    # 제목의 핵심 사건을 요약문에 섞인 부가 실적 표현보다 우선한다.
    for source in (title_text, combined):
        matched = next((label for label, terms in CATEGORY_TERMS if any(term in source for term in terms)), "")
        if matched:
            category = matched
            break

    if direct_in_title and _LOW_VALUE_NEWS.search(title_text):
        return NewsAssessment(
            False, "비투자성 기업 소식", "관련성 낮음",
            "행사·홍보·스포츠 등 투자 판단과 직접 관련이 낮은 기사입니다.",
            relevance_score, 0,
        )

    if direct and is_price_reaction_news(stock_name, title, description, article_body):
        return NewsAssessment(
            False, "시세 반영·시장 요약", "관련성 낮음",
            "새 기업 사건보다 이미 발생한 주가 움직임을 설명하는 기사입니다.",
            relevance_score, 0,
        )

    # 제목·검색 요약이 이미 명확하면 원문 도입부의 과거 배경이나 업종 설명이
    # 그 판정을 뒤집지 못하게 한다. 제목·검색 요약에 방향 표현이 하나도 없을
    # 때만 정제된 도입부를 보조 증거로 더한다.
    matched_positive, matched_negative = _direction_terms(combined)
    if not matched_positive and not matched_negative and article_text:
        # 이미 오른/내린 가격을 전면에 둔 기사는 뒤쪽 애널리스트 의견이나 과거
        # 실적을 새 방향 근거로 승격하지 않는다. 이런 제목도 첫 도입부 계약은 유지한다.
        direction_body = article_text[:350] if _PRICE_REACTION_TITLE.search(title_text) else article_text
        body_positive, body_negative = _body_direction_terms(stock_name, direction_body)
        matched_positive.extend(term for term in body_positive if term not in matched_positive)
        matched_negative.extend(term for term in body_negative if term not in matched_negative)
    positive = sum(POSITIVE_TERMS.get(term, 3) for term in matched_positive)
    negative = sum(NEGATIVE_TERMS[term] for term in matched_negative)
    score = positive - negative
    if not relevant:
        return NewsAssessment(False, category, "관련성 낮음", "종목 직접 언급과 증권 관련 표현이 부족합니다.", relevance_score, score)
    if score >= 4:
        outlook = "호재 가능성 높음"
    elif score >= 2:
        outlook = "호재 가능성"
    elif score <= -4:
        outlook = "악재 가능성 높음"
    elif score <= -2:
        outlook = "악재 가능성"
    else:
        outlook = "판단 보류"
    evidence = matched_positive[:2] + matched_negative[:2]
    reason = f"기사에서 {', '.join(evidence)} 표현을 확인했습니다." if evidence else "방향을 단정할 핵심 표현이 부족합니다."
    return NewsAssessment(True, category, outlook, reason, relevance_score, score)
