"""Colours, ttk styles and status tags shared by every screen."""

import re
import tkinter as tk
from tkinter import ttk


COLORS = {
    "bg": "#f4f6f9", "panel": "#ffffff", "line": "#dfe4ea", "ink": "#1f2933", "muted": "#6b7785",
    "navy": "#24394d", "navy_light": "#3d5468", "blue": "#2f6fde", "blue_light": "#e8f0fd",
    "green": "#1f9d55", "green_light": "#e6f6ec", "amber": "#b7791f", "amber_light": "#fff4dc",
    "red": "#c53030", "red_light": "#fde8e8", "violet": "#6b46c1", "violet_light": "#efe9fb",
    "gray_light": "#eef1f4",
}
FONT = ("Malgun Gothic", 10)
FONT_BOLD = ("Malgun Gothic", 10, "bold")
FONT_TITLE = ("Malgun Gothic", 14, "bold")
FONT_SMALL = ("Malgun Gothic", 9)

TAGS = {
    "ok": (COLORS["green_light"], COLORS["green"]),
    "warn": (COLORS["amber_light"], COLORS["amber"]),
    "bad": (COLORS["red_light"], COLORS["red"]),
    "transfer": (COLORS["violet_light"], COLORS["violet"]),
    "muted": (COLORS["gray_light"], COLORS["muted"]),
}


def apply_style(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(background=COLORS["bg"])
    root.option_add("*Font", FONT)
    for name, value in (("*Text.relief", "flat"), ("*Text.background", COLORS["panel"]),
                        ("*Text.highlightThickness", 1), ("*Text.highlightBackground", COLORS["line"]),
                        ("*Text.highlightColor", COLORS["blue"]), ("*Text.padX", 8), ("*Text.padY", 6),
                        ("*Text.borderWidth", 0), ("*Listbox.relief", "flat"), ("*Listbox.highlightThickness", 1),
                        ("*Listbox.highlightBackground", COLORS["line"])):
        root.option_add(name, value)
    style.configure(".", font=FONT, background=COLORS["bg"], foreground=COLORS["ink"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("Side.TFrame", background=COLORS["panel"])
    style.configure("Header.TFrame", background=COLORS["navy"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["ink"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("Title.TLabel", font=FONT_TITLE)
    style.configure("Panel.TLabel", background=COLORS["panel"])
    style.configure("Header.TLabel", background=COLORS["navy"], foreground="#ffffff", font=FONT_BOLD)
    style.configure("Status.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], padding=(12, 6))
    style.configure("TCheckbutton", background=COLORS["bg"])
    style.configure("Panel.TCheckbutton", background=COLORS["panel"])
    style.configure("TButton", padding=(10, 5), background=COLORS["panel"], bordercolor=COLORS["line"])
    style.map("TButton", background=[("active", COLORS["gray_light"])])
    for name, color, light in (("Primary", "blue", "blue_light"), ("Success", "green", "green_light"),
                               ("Danger", "red", "red_light")):
        style.configure(f"{name}.TButton", background=COLORS[color], foreground="#ffffff",
                        bordercolor=COLORS[color], font=FONT_BOLD, padding=(12, 6))
        style.map(f"{name}.TButton", background=[("active", COLORS[color]), ("disabled", COLORS["line"])],
                  foreground=[("disabled", COLORS["muted"])])
    style.configure("TMenubutton", padding=(10, 5), background=COLORS["panel"])
    style.configure("Small.TButton", padding=(7, 4), width=-4, background=COLORS["panel"], bordercolor=COLORS["line"])
    style.configure("Chip.TRadiobutton", padding=(10, 3), background=COLORS["bg"])
    style.configure("Treeview", rowheight=26, background=COLORS["panel"], fieldbackground=COLORS["panel"],
                    bordercolor=COLORS["line"])
    style.configure("Treeview.Heading", font=FONT_BOLD, background="#fafbfc", foreground=COLORS["muted"],
                    padding=(6, 4))
    style.map("Treeview", background=[("selected", COLORS["blue"])], foreground=[("selected", "#ffffff")])
    style.configure("TPanedwindow", background=COLORS["bg"])
    style.configure("TEntry", padding=(6, 4), fieldbackground=COLORS["panel"], bordercolor=COLORS["line"])
    style.configure("TCombobox", padding=(6, 3), bordercolor=COLORS["line"])
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel"])], foreground=[("readonly", COLORS["ink"])])
    style.configure("Card.TFrame", background=COLORS["panel"], bordercolor=COLORS["line"], relief="solid", borderwidth=1)
    style.configure("Card.TLabel", background=COLORS["panel"])
    style.configure("CardTitle.TLabel", background=COLORS["panel"], foreground=COLORS["ink"], font=FONT_BOLD)
    style.configure("CardHint.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=FONT_SMALL)
    style.configure("Summary.TLabel", background=COLORS["blue_light"], foreground=COLORS["ink"], font=FONT_BOLD,
                    padding=(12, 8))
    style.configure("Chip.TButton", padding=(8, 2), font=FONT_SMALL, background=COLORS["gray_light"])
    return style


def configure_tags(tree):
    for tag, (background, foreground) in TAGS.items():
        tree.tag_configure(tag, background=background, foreground=foreground)


def transaction_tag(status, follow_up=""):
    if follow_up in ("세무사 확인 필요", "증빙 확보 불가"):
        return "warn"
    if status == "금액 대조 완료":
        return "ok"
    if status in ("내부이체 확인", "전액 환불 확인", "계약서 근거 확인", "전월 결제 환불 확인", "일부 환불 · 증빙 연결됨"):
        return "transfer"
    if status in ("증빙 미연결", "연결 PDF 위치 확인"):
        return "bad"
    return "warn"


# Mouse-over explanations for buttons, menu items and sidebar links (label → what it does).
HELP = {
    # 1단계
    "폴더 열기": "인보이스가 있는 폴더를 골라요. 그 폴더의 파일을 모두 보여 주고, 처음 시작할 때 한 번 쓰면 돼요.",
    "파일 가져오기": "인보이스 파일을 직접 보면서 골라요(여러 개 가능). 작업 폴더로 복사(또는 환경설정에 따라 이동)하고 바로 읽어요.",
    "폴더 가져오기": "인보이스가 든 폴더를 고르면 하위 폴더까지 PDF·사진·XML을 작업 폴더로 복사(또는 환경설정에 따라 이동)해요.",
    "작업 폴더 열기 (탐색기)": "인보이스와 명세서가 모이는 작업 폴더를 탐색기로 열어요.",
    "작업 폴더 바꾸기": "작업 폴더 위치를 바꿔요. 이전 폴더의 파일은 옮기지 않아요.",
    "환경설정": "작업 폴더 위치와 가져오기 방식(복사·이동)을 정해요.",
    "환경설정 (작업 폴더·복사/이동)": "작업 폴더 위치와 가져오기 방식(복사·이동)을 정해요.",
    "다른 폴더 열기 (옮기지 않고 보기)": "파일을 옮기지 않고 다른 폴더의 인보이스를 그 자리에서 보고 이름만 바꿔요(이전 방식).",
    "새 인보이스 (미분류)": "증빙 폴더 안 ‘미분류’ 폴더에 새로 넣은 파일만 보여 줘요. 이름을 바꾸고 발행월 폴더로 옮길 수 있는 파일을 미리 골라 둬요.",
    "편집": "한 개를 고르면 발행일·업체명·총액·번호를 직접 고쳐요(더블클릭도 돼요). 여러 개를 고르면 업체명·발행일·통화를 한 번에 넣어요.",
    "파일명 변경": "체크한 파일의 이름을 규칙대로 바꾸고, PDF 내용을 같은 이름의 마크다운(.md)으로 함께 저장해요.",
    "정리하기": "체크한 파일을 발행월 폴더로 옮기고 이름을 바꾼 뒤 마크다운(.md)도 함께 저장해요.",
    "정리하기 (미분류)": "미분류의 체크한 파일을 이름을 바꿔 발행월 폴더로 옮기고 마크다운(.md)도 함께 저장해요.",
    "원본·내용 보기": "왼쪽에 PDF, 오른쪽에 읽어 낸 내용(마크다운)을 함께 보여 줘요. 숫자가 맞는지 눈으로 확인할 때 써요.",
    "중복 정리": "같은 인보이스가 두 번 저장된 파일을 찾아요. 하나만 남기고 나머지는 ‘중복’ 폴더로 옮겨요. 지우지 않아요.",
    "중복 인보이스 정리": "같은 인보이스가 두 번 저장된 파일을 찾아요. 하나만 남기고 나머지는 ‘중복’ 폴더로 옮겨요. 지우지 않아요.",
    "인보이스 아닌 파일 → 제외 폴더": "광고·약관처럼 인보이스가 아닌 파일을 ‘제외’ 폴더로 옮겨요. 지우지 않고, 정리 되돌리기로 돌릴 수 있어요.",
    "배운 인보이스 양식": "편집에서 한 번 고친 업체의 인보이스 양식 목록이에요. 같은 업체 파일은 이 양식으로 자동으로 읽어요. 잘못 배운 양식은 지울 수 있어요.",
    "마크다운 다시 만들기": "선택한 파일의 마크다운(.md)을 다시 만들어요. 값을 고친 뒤 내용을 새로 맞출 때 써요. 보통은 파일명 변경 때 자동으로 저장돼요.",
    "정리 되돌리기": "마지막으로 이름을 바꾸거나 옮긴 파일을 원래 이름·위치로 되돌려요. 마크다운도 함께 돌아가요.",
    "하위 폴더 포함": "고른 폴더 아래 폴더에 있는 파일까지 함께 보여 줘요.",
    "발행월 폴더로 이동": "파일명 변경 때 발행일 기준 ‘2026-11’ 같은 월 폴더로도 옮겨요.",
    # 2단계
    "은행·카드 명세서 추가": "은행·카드 명세서(CSV 또는 PDF) 한 개를 불러와요. 원본은 증빙 폴더 ‘명세서/월’ 폴더로 옮겨 보관해요.",
    "여러 명세서 한 번에 추가": "명세서 여러 개를 한 번에 골라 불러와요. 처음 보는 양식만 열 설정을 해 주면 돼요.",
    "이어서 작업": "마지막으로 작업하던 명세서와 증빙 폴더를 다시 열어요.",
    "지난 달 열기": "예전에 불러온 명세서를 월별로 보여 줘요. 고르면 그달을 다시 열어 보고 고칠 수 있어요.",
    "예시 명세서 불러오기": "연습 모드용 예시 카드 명세서를 불러와요.",
    "명세서 양식 관리": "저장된 명세서 양식(은행·카드별 열 설정)을 보고, 이름을 바꾸거나 지워요.",
    "번호 표시 PDF 명세서 보기": "PDF 명세서의 금액 옆에 거래번호를 찍은 사본을 열어 봐요. 원본은 그대로예요.",
    "명세서 기간 점검": "명세서끼리 빠진 날짜나 겹치는 기간이 없는지 확인해요.",
    # 3단계
    "증빙 폴더 선택": "인보이스가 있는 증빙 폴더를 골라요. 1단계에서 연 폴더가 있으면 자동으로 써요.",
    "최종 검수": "출력하지 않고 화면에서 검수해요. 거래마다 명세서 원문과 인보이스를 나란히 보고 Enter(검수 완료)·R(다시 확인). 자동 점검이 금액·업체명·날짜·중복 연결을 먼저 짚어 줘요.",
    "사유 관리 (한국어·독일어·추가)": "‘증빙 없이 처리’ 사유를 고쳐요. 세무사에게 가는 독일어 문장, 완료로 볼지 여부, 내 사유 추가·숨기기.",
    "명세서에서 보기": "고른 거래가 명세서 PDF의 어디에 있는지 그 부분을 보여 줘요. CSV 명세서는 그 행의 모든 칸을 보여 줘요.",
    "증빙 값 고치기": "오른쪽 목록에서 고른 증빙의 발행일·업체명·총액·번호를 고쳐요. 파일 내용은 바꾸지 않아요.",
    "증빙 → 제외 폴더": "인보이스가 아니거나 이번 마감과 상관없는 증빙을 ‘제외’ 폴더로 옮겨요. 지우지 않아요.",
    "연결 안 된 증빙": "아직 어떤 거래에도 연결되지 않은 인보이스 목록이에요. 열어 보고, 값을 고치고, 거래에 연결하거나 ‘제외’ 폴더로 보낼 수 있어요.",
    "+ 증빙 파일 추가": "고른 거래의 인보이스 파일을 골라요. 작업 폴더로 가져오고(환경설정의 복사·이동) 바로 이 거래에 연결해요.",
    "월세·할부·대출·수수료 (계약서·명세서 근거)": "인보이스가 따로 없는 거래(월세·할부·대출 상환·수수료 등)를 계약서나 명세서 근거로 처리해요. ‘다음부터 자동 적용’을 켜면 같은 상대방 거래에 다음 명세서부터 자동으로 적용돼요.",
    "자동 적용 규칙 보기·삭제": "월세·할부·대출처럼 매달 자동이체되는 거래에 등록한 규칙을 보고 지워요.",
    "일괄 매칭": "금액·날짜가 1:1로 맞는 거래와 증빙을 모아 보여 줘요. 체크한 것을 한 번에 연결해요.",
    "한 번에 연결하기": "금액·날짜가 1:1로 맞는 후보를 모아 보여 줘요. ‘확실’은 미리 체크돼 있어요.",
    "매칭완료 폴더로 이동": "연결이 끝난 증빙을 ‘매칭완료_월’ 폴더로 옮기고 이름 앞에 [거래번호]를 붙여요. 마크다운도 함께 옮겨요.",
    "명세서 근거로 처리 (수수료 등)": "은행 수수료처럼 따로 인보이스가 없는 거래를 명세서를 근거로 처리해요. 사유를 남겨요.",
    "이동 되돌리기": "마지막 매칭완료 이동을 원래 폴더·이름으로 되돌려요.",
    "증빙 없이 처리": "인보이스 없이 처리하는 거래: 명세서 근거, 카드대금·계좌 이체, 결제·환불 묶기.",
    "카드대금·내부이체 후보": "카드대금 자동이체처럼 내 계좌끼리 오간 거래를 짝지어요. 인보이스가 필요 없어요.",
    "결제·환불 연결": "결제와 그 환불 거래를 묶어요. 거래 두 건 이상을 Ctrl로 함께 고른 뒤 누르세요.",
    "메모·기록": "거래에 내 메모, 작업 기록, 후속 상태, 세무사 전달 메모(독일어)를 남겨요.",
    "인보이스 요청 초안": "업체에 인보이스를 요청하는 독일어 메일 초안을 만들어요.",
    "Gmail 검색": "거래 날짜 전후의 첨부 PDF를 Gmail에서 찾는 검색을 브라우저로 열어요.",
    "선택 연결": "왼쪽 거래와 오른쪽에서 고른 증빙을 연결해요.",
    "연결": "추천 1순위 증빙을 이 거래에 바로 연결해요.",
    "해제": "선택한 증빙과 거래의 연결을 풀어요.",
    "나란히 보기": "명세서 거래 줄과 인보이스를 한 화면에 나란히 보여 줘요.",
    "열기": "선택한 증빙 파일을 열어요.",
    "전체 증빙에서 고르기": "금액이 달라 추천에 없는 증빙까지 모두 보여 줘요. 부분결제·환율 차이 때 써요.",
    "배분·차액": "한 증빙을 여러 거래에 나누거나, 금액 차이(수수료·환율·할인)의 사유를 적어요.",
    "정정·원본 연결": "수정 인보이스(Gutschrift·Storno)를 원래 인보이스와 연결해요.",
    "접대비 정보": "접대비(Bewirtung) 증빙에 날짜·장소·참석자·목적을 적어요.",
    "보조 자료 첨부": "확인 메일 같은 보조 자료를 거래에 붙여요. ZIP의 Anlagen 폴더에 들어가요.",
    "증빙 전달 메모 (독일어)": "이 증빙에 대해 세무사에게 전할 독일어 메모를 적어요.",
    "다음 단계: 누락 점검": "4단계로 넘어가 빠진 증빙과 확인할 거래를 점검해요.",
    "확인 필요": "아직 증빙이 없거나 확인할 것이 남은 거래만 보여 줘요. 연결이 끝나면 여기서 빠져요.",
    "전체": "모든 거래를 보여 줘요.",
    "증빙 없음": "증빙이 하나도 연결되지 않은 거래만 보여 줘요.",
    "대조 완료": "증빙 연결과 금액 대조가 끝난 거래를 보여 줘요.",
    # 4단계
    "월별 누락 점검표": "월마다 증빙 없는 거래, 거래 없는 증빙, 번역 대기 메모, 명세서 공백을 한눈에 보여 줘요.",
    "미연결 증빙": "어느 거래에도 연결되지 않은 인보이스를 보여 줘요.",
    "후속 목록": "‘요청 중’·‘세무사 확인 필요’ 같은 후속 상태가 남은 거래를 모아 봐요.",
    "전체 월 검색": "모든 달의 거래와 증빙을 업체명·번호·금액으로 찾아요.",
    # 5단계
    "세무사용 ZIP 만들기": "제출할 월·거래를 고르고, 넣을 파일을 확인한 뒤 세무사용 ZIP을 만들어요.",
    "추가 제출 ZIP": "이미 보낸 마감본 이후 바뀐 거래와 새 증빙만 담아 보내요.",
    "마감본 이력·전달 상태": "만든 ZIP 목록과 ‘전달함’·‘회신 대기’ 같은 상태를 관리해요.",
    "월 마감 확정·재열기": "보낸 달을 잠가 실수로 바뀌지 않게 하거나, 다시 열어요.",
    "CSV 내보내기": "거래 대조표를 CSV 파일 하나로 저장해요.",
    # 도구
    "작업 기록 백업": "연결·메모 같은 작업 기록만 파일 하나로 백업해요.",
    "작업 기록 복원": "백업한 작업 기록을 되살려요.",
    "전체 작업 백업": "작업 기록과 증빙·명세서 파일까지 모두 ZIP 하나로 백업해요. 다른 PC로 옮길 때 써요.",
    "전체 작업 복원": "전체 작업 백업 ZIP을 풀어 그대로 이어서 작업해요.",
    "연습 모드 열기": "예시 자료로 연습해요. 실제 파일과 기록은 건드리지 않아요.",
    "설치·작동 점검": "폴더 쓰기, PDF 읽기, OCR, ZIP 저장이 되는지 점검해요.",
    "오류 진단 저장": "문제가 생겼을 때 보낼 진단 파일을 저장해요. 개인 파일 내용은 넣지 않아요.",
    "사용법 열기": "그림이 있는 사용법을 열어요.",
    # 메모·문구
    "✎ 문구 관리": "세무사 전달 메모에 넣는 독일어 문구를 추가·수정·삭제해요.",
    "상태·예정일로 독일어 초안 만들기": "후속 상태와 예정일로 세무사 전달 메모 독일어 초안을 만들어요.",
    "명세서 추가": "은행·카드 명세서(CSV 또는 PDF) 한 개를 불러와요. 원본은 증빙 폴더 ‘명세서/월’ 폴더로 옮겨 보관해요.",
    "여러 개 추가": "명세서 여러 개를 한 번에 골라 불러와요. 처음 보는 양식만 열 설정을 해 주면 돼요.",
    "번호 명세서": "PDF 명세서의 금액 옆에 거래번호를 찍은 사본을 열어요. 인쇄용이에요. 원본은 그대로예요.",
    "계좌 순서로 정렬": "정해 둔 계좌 순서(예: Deutsche Bank → Amex)대로 명세서 목록을 정렬해요. 위에 있는 명세서부터 번호가 매겨져요.",
    "계좌 순서 정하기": "은행·카드 계좌의 번호 순서를 정해요. 새 계좌는 맨 아래에 붙어요. 환경설정 파일에 저장돼요.",
    "▲ 위로": "고른 명세서를 한 칸 위로 옮겨요.",
    "▼ 아래로": "고른 명세서를 한 칸 아래로 옮겨요.",
    "인쇄 묶음": "명세서 쪽마다 그 쪽 번호의 인보이스를 이어 붙인 PDF 한 개를 열어요. 인보이스 아래 가운데에 거래번호가 찍혀요. 한 번에 인쇄할 때 써요. 원본은 그대로예요.",
    "메일 검색": "거래 금액(30,27처럼 독일식)이나 업체명으로 거래일 앞뒤의 메일을 찾아요. Gmail·한메일 중 골라 브라우저로 열어요.",
    "메일 등록 (Gmail·한메일)": "메일 검색에서 고를 메일함(Gmail 계정, 한메일)을 등록해요.",
    "메일 등록…": "메일 검색에서 고를 메일함(Gmail 계정, 한메일)을 등록해요.",
    "브라우저에서 찾기": "고른 메일함의 검색 결과를 브라우저로 열어요.",
    "원본에서 보기·값 고르기": "인보이스 원본을 쪽 넘기기·확대로 보며, 값을 마우스로 끌어 고르거나 직접 적어 편집 칸에 넣어요.",
    "편집 칸에 넣기": "이 값을 편집 창의 해당 칸에 넣어요. 저장은 편집 창에서 해요.",
    "폭 맞춤": "쪽 너비를 창에 맞춰요.",
    "쪽 맞춤": "쪽 전체가 한눈에 보이게 맞춰요.",
    "다른 달": "거래일에서 45일 넘게 떨어진, 금액이 같은 증빙도 후보에 보여 줘요(매달 같은 금액인 택배·구독 등).",
    "지난 달": "예전에 불러온 명세서를 월별로 보여 줘요. 고르면 그달을 다시 열어 보고 고칠 수 있어요.",
    "증빙 폴더": "인보이스가 있는 증빙 폴더를 골라요. 1단계에서 연 폴더가 있으면 자동으로 써요.",
    "완료 폴더로 이동": "연결이 끝난 증빙을 ‘매칭완료_월’ 폴더로 옮기고 이름 앞에 [거래번호]를 붙여요. 마크다운도 함께 옮겨요.",
    "누락 점검표": "월마다 증빙 없는 거래, 거래 없는 증빙, 번역 대기 메모, 명세서 공백을 한눈에 보여 줘요.",
    "후속 할 일": "‘요청 중’·‘세무사 확인 필요’ 같은 후속 상태가 남은 거래를 모아 봐요.",
    "기간 점검": "명세서끼리 빠진 날짜나 겹치는 기간이 없는지 확인해요.",
    "전체 검색": "모든 달의 거래와 증빙을 업체명·번호·금액으로 찾아요.",
    "세무사 ZIP": "제출할 월·거래를 고르고, 넣을 파일을 확인한 뒤 세무사용 ZIP을 만들어요.",
    "추가 ZIP": "이미 보낸 마감본 이후 바뀐 거래와 새 증빙만 담아 보내요.",
    "전달 이력": "만든 ZIP 목록과 ‘전달함’·‘회신 대기’ 같은 상태를 관리해요.",
    "마감 확정·해제": "보낸 달을 잠가 실수로 바뀌지 않게 하거나, 다시 열어요.",
    "CSV 저장": "거래 대조표를 CSV 파일 하나로 저장해요.",
}


def help_key(text):
    """Label text without counts, arrows and drop-down marks, for HELP lookups."""
    text = re.sub(r"\s*\(\d+건?\)", "", str(text))
    text = re.sub(r"\s*\((?:복사|이동)\)", "", text)
    text = re.sub(r"\s*[▾→]+\s*$", "", text).strip()
    return re.sub(r"\s+\d+$", "", text)


class Tooltip:
    """Small explanation bubble shown while the mouse rests on a widget."""

    current = None

    def __init__(self, widget, text_for):
        self.widget, self.text_for, self.job, self.tip = widget, text_for, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, _event=None):
        self.cancel()
        self.job = self.widget.after(450, self.show)

    def cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def show(self, x=None, y=None):
        self.job = None
        text = self.text_for()
        if not text or not self.widget.winfo_exists():
            return
        Tooltip.close_current()
        # next to the mouse pointer (v1.10): the widget's own position can be far away on scaled Windows screens
        self.tip = show_bubble(self.widget, text,
                               x if x is not None else self.widget.winfo_pointerx() + 14,
                               y if y is not None else self.widget.winfo_pointery() + 20)
        Tooltip.current = self

    def hide(self, _event=None):
        self.cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None

    @classmethod
    def close_current(cls):
        if cls.current is not None:
            cls.current.hide()
            cls.current = None


def show_bubble(widget, text, x, y):
    tip = tk.Toplevel(widget)
    tip.wm_overrideredirect(True)
    try:
        tip.attributes("-topmost", True)
    except tk.TclError:
        pass
    tk.Label(tip, text=text, justify="left", wraplength=320, background="#fffbe6", foreground=COLORS["ink"],
             relief="solid", borderwidth=1, font=FONT_SMALL, padx=8, pady=5).pack()
    tip.update_idletasks()
    window = (widget.master if isinstance(widget, tk.Menu) and widget.master else widget).winfo_toplevel()
    # keep it inside the program window (also on a second monitor)
    right = window.winfo_rootx() + window.winfo_width()
    bottom = window.winfo_rooty() + window.winfo_height()
    if window.winfo_width() > 200:
        x = min(x, right - tip.winfo_reqwidth() - 8)
        y = min(y, bottom - tip.winfo_reqheight() - 8)
    tip.wm_geometry(f"+{x}+{y}")
    tip.after(8000, lambda: tip.winfo_exists() and tip.destroy())  # never leave a bubble behind
    return tip


def attach_help(widget):
    """Give every button, check box, choice and sidebar link with a HELP entry a mouse-over bubble."""
    if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Menubutton, tk.Label)) \
            and not getattr(widget, "_help_attached", False):
        try:
            label = str(widget.cget("text"))
        except tk.TclError:
            label = ""
        if label and (HELP.get(help_key(label)) or not isinstance(widget, tk.Label)):
            widget._help_attached = True
            Tooltip(widget, lambda w=widget: HELP.get(help_key(w.cget("text")), ""))
    for child in widget.winfo_children():
        attach_help(child)


def menu_help(menu):
    """Explanations for drop-down menu entries, shown beside the highlighted entry."""
    state = {"tip": None}

    def close(_event=None):
        if state["tip"] is not None:
            try:
                state["tip"].destroy()
            except tk.TclError:
                pass
            state["tip"] = None

    def selected(_event=None):
        close()
        try:
            index = menu.index("active")
        except tk.TclError:
            return
        if index is None or menu.type(index) != "command":
            return
        text = HELP.get(help_key(menu.entrycget(index, "label")))
        if text:
            x = menu.winfo_pointerx() + 24
            y = menu.winfo_pointery() + 12
            state["tip"] = show_bubble(menu, text, x, y)

    menu.bind("<<MenuSelect>>", selected, add="+")
    menu.bind("<Unmap>", close, add="+")


def more_menu(parent, label, items):
    """A '더보기' button that opens a drop-down list of (label, command)."""
    button = ttk.Menubutton(parent, text=label)
    menu = tk.Menu(button, tearoff=False)
    for text, command in items:
        if text == "-":
            menu.add_separator()
        else:
            menu.add_command(label=text, command=lambda c=command: (Tooltip.close_current(), c()))
    button["menu"] = menu
    menu_help(menu)
    return button


PRIMARY_ENDINGS = ("저장", "적용", "사용", "연결")


class InlineDialog(tk.Frame):
    """A detail panel that opens inside the main window (right side) instead of a separate window.

    It accepts the Toplevel calls the screens already use (title, geometry, transient, grab_set,
    protocol, destroy, bind), so a screen only swaps tk.Toplevel for InlineDialog.
    """

    def __init__(self, parent, **_options):
        root = parent.winfo_toplevel()
        super().__init__(root, background=COLORS["panel"], highlightthickness=1,
                         highlightbackground="#9aa3ad", highlightcolor="#9aa3ad")
        self._close_command = None
        self._width = None
        self._centered = None  # (width, height) when the panel floats in the middle
        self._moved = None  # (x, y) after the user dragged it by its title bar
        self._tag = f"InlineDialog{id(self)}"
        self._previous_focus = root.focus_get()
        header = tk.Frame(self, background=COLORS["navy"], padx=14, pady=9)
        header.pack(side="top", fill="x")
        self._title = tk.Label(header, text="", background=COLORS["navy"], foreground="#ffffff",
                               font=("Malgun Gothic", 12, "bold"), anchor="w")
        self._title.pack(side="left", fill="x", expand=True)
        close = tk.Label(header, text="✕  닫기", background=COLORS["navy"], foreground="#d5dee8",
                         cursor="hand2", font=FONT)
        close.pack(side="right")
        close.bind("<Button-1>", lambda event: self._request_close())
        self._header = header
        for handle in (header, self._title):
            handle.configure(cursor="fleur")
            handle.bind("<ButtonPress-1>", self._drag_start)
            handle.bind("<B1-Motion>", self._drag)
        self.configure(background=COLORS["bg"])
        self._area = getattr(root, "dialog_area", None) or root
        self._place()
        self.lift()
        self.bind_class(self._tag, "<Escape>", lambda event: self._request_close())
        self.bind_class(self._tag, "<ButtonPress>", self._outside_click, add="+")
        self.after(30, self._finish)

    # Toplevel-compatible calls ------------------------------------------
    def title(self, text=None):
        if text is None:
            return self._title.cget("text")
        self._title.configure(text=text)
        return None

    def geometry(self, spec=None):
        if spec:
            size = spec.split("+")[0]
            if "x" in size:
                self._width = int(size.split("x")[0])
                self._place()
        return ""

    def transient(self, _master=None):
        return None

    def resizable(self, *_args):
        return None

    def minsize(self, *_args):
        return None

    def protocol(self, name, command=None):
        if name == "WM_DELETE_WINDOW":
            self._close_command = command

    def bind(self, sequence=None, func=None, add=None):
        if sequence and func:
            self.bind_class(self._tag, sequence, func, add)
            self.after_idle(self._retag)
            return None
        return super().bind(sequence, func, add)

    def destroy(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        previous = self._previous_focus
        super().destroy()
        try:
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
        except (tk.TclError, KeyError):
            pass

    # Layout --------------------------------------------------------------
    def _available_width(self):
        width = self._area.winfo_width()
        if width <= 1:
            self._area.update_idletasks()
            width = self._area.winfo_width()
        return max(420, width - 12)

    def _place(self):
        width = min(self._width or 640, self._available_width())
        if self._moved:
            self.place_forget()
            self.place(in_=self._area, x=self._moved[0], y=self._moved[1], anchor="nw",
                       width=self.winfo_width() or width, height=self.winfo_height() or 400)
        elif self._centered:
            area_height = max(300, self._area.winfo_height() - 24)
            self.place_forget()
            self.place(in_=self._area, relx=0.5, rely=0.5, anchor="center",
                       width=min(self._centered[0], self._available_width()), height=min(self._centered[1], area_height))
        else:
            self.place(in_=self._area, relx=1.0, x=-6, y=6, anchor="ne", relheight=1.0, height=-12, width=width)

    def center(self, width, height):
        """Float in the middle of the window instead of filling the right side (the title bar can still drag it)."""
        self._centered = (width, height)
        self._width = width
        self._place()

    # Moving by the title bar ---------------------------------------------
    def _drag_start(self, event):
        self._drag_from = (event.x_root - self.winfo_rootx(), event.y_root - self.winfo_rooty())

    def _drag(self, event):
        start = getattr(self, "_drag_from", None)
        if start is None:
            return
        width, height = self.winfo_width(), self.winfo_height()
        area = self._area
        x = event.x_root - area.winfo_rootx() - start[0]
        y = event.y_root - area.winfo_rooty() - start[1]
        x = max(-width + 120, min(x, area.winfo_width() - 120))  # always keep part of the title bar reachable
        y = max(0, min(y, area.winfo_height() - 40))
        self._moved = (x, y)
        self.place_forget()
        self.place(in_=area, x=x, y=y, anchor="nw", width=width, height=height)

    # A click outside while this panel holds the window -----------------
    def _outside_click(self, event):
        try:
            if self.grab_current() is not self:
                return None
            inside = self.winfo_containing(event.x_root, event.y_root)
        except (tk.TclError, KeyError):
            return None
        if inside is not None and str(inside).startswith(str(self)):
            return None
        Tooltip.close_current()
        name = self.title() or "이 창"
        show_bubble(self, f"‘{name}’ 창이 열려 있어요.\n저장하거나 오른쪽 위 ‘✕ 닫기’로 닫은 뒤 다른 버튼을 누르세요.",
                    event.x_root + 12, event.y_root + 12)
        self._flash(4)
        return "break"

    def _flash(self, times):
        if times <= 0 or not self.winfo_exists():
            return
        color = COLORS["red"] if times % 2 == 0 else COLORS["navy"]
        for widget in (self._header, self._title):
            widget.configure(background=color)
        self.after(180, lambda: self._flash(times - 1))

    def _finish(self):
        if not self.winfo_exists():
            return
        if self._width is None:
            self.update_idletasks()
            self._width = max(520, self.winfo_reqwidth() + 24)
            self._place()
        self._style_buttons(self)
        attach_help(self)
        self._retag()
        self.focus_set()

    def _style_buttons(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button) and str(child.cget("style")) in ("", "TButton"):
                text = str(child.cget("text"))
                if (text.endswith(PRIMARY_ENDINGS) or text == "확인") and "해제" not in text:
                    child.configure(style="Primary.TButton")
            self._style_buttons(child)

    def _retag(self):
        if not self.winfo_exists():
            return
        stack = [self]
        while stack:
            widget = stack.pop()
            tags = widget.bindtags()
            if self._tag not in tags:
                widget.bindtags((self._tag, *tags))
            stack.extend(widget.winfo_children())

    def _request_close(self):
        if self._close_command:
            self._close_command()
        else:
            self.destroy()


class Busy:
    """Overlay in the middle of the window while long work runs, e.g.
        with Busy(root, "증빙을 읽고 있어요") as busy:
            for index, item in enumerate(items, 1):
                busy.step(index, len(items), item.name)
    Clicks are held while it shows; the window keeps repainting."""

    _active = None

    def __init__(self, parent, text):
        self.root = parent.winfo_toplevel()
        outer = Busy._active
        self.nested = outer is not None and outer.frame.winfo_exists()
        if self.nested:  # work inside other work: reuse the overlay that is already showing
            self.frame, self.title, self.detail, self.bar = outer.frame, outer.title, outer.detail, outer.bar
            self.title.configure(text=text)
            self.root.update()
            return
        Busy._active = self
        self.frame = tk.Frame(self.root, background=COLORS["panel"], highlightthickness=2,
                              highlightbackground=COLORS["blue"], padx=26, pady=18)
        self.title = tk.Label(self.frame, text=text, font=FONT_BOLD, background=COLORS["panel"])
        self.title.pack(anchor="w")
        self.detail = tk.Label(self.frame, text="잠시만 기다려 주세요…", background=COLORS["panel"],
                               foreground=COLORS["muted"], width=46, anchor="w")
        self.detail.pack(anchor="w", pady=(4, 8))
        self.bar = ttk.Progressbar(self.frame, length=360, mode="indeterminate")
        self.bar.pack(fill="x")
        self.frame.place(relx=0.5, rely=0.45, anchor="center")
        self.frame.lift()
        try:
            self.frame.grab_set()
        except tk.TclError:
            pass
        self.root.configure(cursor="watch")
        self.root.update()

    def step(self, index=None, total=None, text=""):
        if not self.frame.winfo_exists():
            return
        if total:
            self.bar.configure(mode="determinate", maximum=total, value=index or 0)
            self.detail.configure(text=f"{index}/{total} · {text}"[:60] if text else f"{index}/{total}")
        else:
            self.bar.step(4)
            if text:
                self.detail.configure(text=text[:60])
        self.root.update()

    def close(self):
        if self.nested:
            return
        Busy._active = None
        try:
            self.frame.grab_release()
            self.frame.destroy()
            self.root.configure(cursor="")
        except tk.TclError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def show_notice(parent, message, title="안내", kind="info", action=None):
    """A small pop-up card in the middle of the main window. Enter, Esc or 확인 closes it.

    action: optional (label, command) shown as the main button, e.g. ("증빙 매칭으로 이동 →", go)."""
    root = parent.winfo_toplevel()
    accent = {"info": COLORS["blue"], "warn": COLORS["amber"], "error": COLORS["red"]}.get(kind, COLORS["blue"])
    card = tk.Frame(root, background=COLORS["panel"], highlightthickness=1, highlightbackground="#9aa3ad",
                    highlightcolor="#9aa3ad")
    tk.Frame(card, background=accent, width=6).pack(side="left", fill="y")
    body = tk.Frame(card, background=COLORS["panel"], padx=18, pady=14)
    body.pack(side="left", fill="both", expand=True)
    tk.Label(body, text=title, font=FONT_BOLD, background=COLORS["panel"], foreground=accent,
             anchor="w").pack(fill="x")
    tk.Label(body, text=message, font=FONT, background=COLORS["panel"], foreground=COLORS["ink"],
             wraplength=380, justify="left", anchor="w").pack(fill="x", pady=(6, 12))
    previous = root.focus_get()

    def close(_event=None):
        try:
            card.grab_release()
        except tk.TclError:
            pass
        card.destroy()
        try:
            if previous is not None and previous.winfo_exists():
                previous.focus_set()
        except (tk.TclError, KeyError):
            pass

    buttons = tk.Frame(body, background=COLORS["panel"])
    buttons.pack(anchor="e")
    if action:
        label, command = action

        def run(_event=None):
            close()
            command()

        button = ttk.Button(buttons, text=label, style="Primary.TButton", command=run)
        ttk.Button(buttons, text="닫기", command=close).pack(side="left", padx=(0, 6))
        button.pack(side="left")
        button.bind("<Return>", run)
    else:
        button = ttk.Button(buttons, text="확인", style="Primary.TButton", command=close)
        button.pack(side="left")
    card.place(relx=0.5, rely=0.42, anchor="center")
    card.lift()
    for widget in (card, body, button):
        if not action or widget is not button:
            widget.bind("<Return>", close)
        widget.bind("<Escape>", close)
    card.after_idle(button.focus_set)
    try:
        card.grab_set()
    except tk.TclError:
        pass
    return card


def ask_yes_no(title, message, parent=None, warnings=(), undo=None, ok="확인", cancel="취소", choices=None):
    """Confirmation card in the middle of the main window (replaces the system yes/no box).

    message: what will happen; warnings: lines shown in amber ("주의"); undo: True / False / text about undoing.
    choices: optional [(label, value), ...] radio buttons; then the chosen value is returned instead of True.
    Returns False (or None with choices) when cancelled. Enter = 확인, Esc = 취소."""
    root = (parent or tk._default_root).winfo_toplevel()
    accent = COLORS["amber"] if warnings else COLORS["blue"]
    card = tk.Frame(root, background=COLORS["panel"], highlightthickness=1, highlightbackground="#9aa3ad",
                    highlightcolor="#9aa3ad")
    tk.Frame(card, background=accent, width=6).pack(side="left", fill="y")
    body = tk.Frame(card, background=COLORS["panel"], padx=18, pady=14)
    body.pack(side="left", fill="both", expand=True)
    tk.Label(body, text=title, font=FONT_BOLD, background=COLORS["panel"], foreground=accent, anchor="w").pack(fill="x")
    tk.Label(body, text=message, font=FONT, background=COLORS["panel"], foreground=COLORS["ink"],
             wraplength=520, justify="left", anchor="w").pack(fill="x", pady=(6, 8))
    picked = tk.StringVar(value=str(choices[0][1]) if choices else "")
    if choices:
        for label, value in choices:
            ttk.Radiobutton(body, text=label, value=str(value), variable=picked).pack(anchor="w")
    if warnings:
        box = tk.Frame(body, background="#fff6e5", padx=10, pady=6)
        box.pack(fill="x", pady=(4, 8))
        tk.Label(box, text="주의", font=FONT_BOLD, background="#fff6e5", foreground=COLORS["amber"], anchor="w").pack(fill="x")
        for line in warnings:
            tk.Label(box, text=f"• {line}", font=FONT_SMALL, background="#fff6e5", foreground=COLORS["ink"],
                     wraplength=500, justify="left", anchor="w").pack(fill="x")
    if undo is not None:
        text = undo if isinstance(undo, str) else ("나중에 되돌릴 수 있어요." if undo else "되돌릴 수 없어요.")
        tk.Label(body, text=text, font=FONT_SMALL, background=COLORS["panel"], foreground=COLORS["muted"],
                 anchor="w").pack(fill="x", pady=(0, 8))
    answer = {"value": None}
    done = tk.BooleanVar(value=False)
    previous_grab = root.grab_current()
    previous = root.focus_get()

    def finish(value):
        answer["value"] = value
        done.set(True)

    buttons = tk.Frame(body, background=COLORS["panel"])
    buttons.pack(anchor="e")
    ttk.Button(buttons, text=cancel, command=lambda: finish(False)).pack(side="left", padx=(0, 6))
    ok_button = ttk.Button(buttons, text=ok, style="Primary.TButton", command=lambda: finish(True))
    ok_button.pack(side="left")
    card.place(relx=0.5, rely=0.42, anchor="center")
    card.lift()
    for widget in (card, body, ok_button):
        widget.bind("<Return>", lambda _event: finish(True))
        widget.bind("<Escape>", lambda _event: finish(False))
    card.after_idle(ok_button.focus_set)
    try:
        card.grab_set()
    except tk.TclError:
        pass
    card.wait_variable(done)
    try:
        card.grab_release()
    except tk.TclError:
        pass
    card.destroy()
    try:
        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()
        if previous is not None and previous.winfo_exists():
            previous.focus_set()
    except (tk.TclError, KeyError):
        pass
    if choices:
        return picked.get() if answer["value"] else None
    return bool(answer["value"])


def show_toast(parent, message, action=None, seconds=6):
    """A short note at the bottom of the window that goes away by itself; action=(label, command) e.g. 되돌리기."""
    root = parent.winfo_toplevel()
    for old in [child for child in root.place_slaves() if getattr(child, "_toast", False)]:
        old.destroy()
    bar = tk.Frame(root, background="#24313f", padx=14, pady=8)
    bar._toast = True
    tk.Label(bar, text=message, background="#24313f", foreground="white", font=FONT).pack(side="left")
    if action:
        label, command = action

        def run():
            bar.destroy()
            command()
        tk.Button(bar, text=label, command=run, relief="flat", background="#3b82f6", foreground="white",
                  activebackground="#2563eb", activeforeground="white", padx=10).pack(side="left", padx=(12, 0))
    bar.place(relx=0.5, rely=0.96, anchor="s")
    bar.lift()
    bar.after(seconds * 1000, lambda: bar.winfo_exists() and bar.destroy())
    return bar


# Tables: sort by header click, alignment and widths the user sets are remembered ---------------
RIGHT_HEADS = ("금액", "총액", "합계", "차액", "잔액", "Betrag")
CENTER_HEADS = ("번호", "거래일", "발행일", "날짜", "월", "거래번호", "기간", "건수", "개수")
ARROWS = (" ▲", " ▼")


def _table_settings():
    try:
        from invoice_renamer import load_settings
        return load_settings().get("tables", {})
    except Exception:  # settings are a convenience; a table must work without them
        return {}


def _save_table_settings(name, entry):
    try:
        from invoice_renamer import load_settings, save_settings
        tables = load_settings().get("tables", {})
        if entry:
            tables[name] = entry
        else:
            tables.pop(name, None)
        save_settings({"tables": tables})
    except Exception:
        pass


def default_anchor(label):
    label = str(label)
    if any(head in label for head in RIGHT_HEADS):
        return "e"
    if label.strip() in CENTER_HEADS:
        return "center"
    return "w"


def sort_value(value):
    """Numbers (also ‘-12.50 EUR’) by size, everything else as text; empty cells last."""
    text = str(value).strip()
    if not text:
        return (2, 0, "")
    match = re.match(r"^[+-]?\d[\d,]*(?:\.\d+)?(?=\s|$)", text)
    if match:
        try:
            return (0, float(match[0].replace(",", "")), text)
        except ValueError:
            pass
    return (1, 0, text.casefold())


def table_tools(tree, name, stretch=None, skip=()):
    """Header click sorts (again: reverse, third time: original order). Right-click on a header sets the
    alignment. Column widths and alignment are kept in settings.json under ‘tables’ → name."""
    tree._tools = {"name": name, "sort": None, "skip": set(skip), "labels": {}}
    saved = _table_settings().get(name, {})
    columns = list(tree["columns"])
    if stretch is None:
        stretch = max(columns, key=lambda column: int(tree.column(column, "width")))
    for column in columns:
        label = str(tree.heading(column, "text")) or column
        tree._tools["labels"][column] = label
        if column in tree._tools["skip"]:
            continue
        tree.column(column, anchor=saved.get("align", {}).get(column) or default_anchor(label), stretch=column == stretch)
        width = saved.get("width", {}).get(column)
        if isinstance(width, int) and width >= 30:
            tree.column(column, width=width)
        tree.heading(column, anchor="center", command=lambda c=column: sort_table(tree, c, toggle=True))

    def column_at(x):
        number = tree.identify_column(x)
        if not number.startswith("#"):
            return None
        shown = tree["displaycolumns"]
        shown = columns if not shown or tuple(shown) == ("#all",) else list(shown)
        index = int(number[1:]) - 1
        return shown[index] if 0 <= index < len(shown) else None

    def press(event):
        tree._tools["resizing"] = tree.identify_region(event.x, event.y) == "separator"

    def release(_event):
        if tree._tools.pop("resizing", False):
            entry = _table_settings().get(name, {})
            entry["width"] = {column: int(tree.column(column, "width")) for column in columns
                              if column not in tree._tools["skip"]}
            _save_table_settings(name, entry)

    def set_anchor(column, anchor):
        tree.column(column, anchor=anchor)
        entry = _table_settings().get(name, {})
        entry.setdefault("align", {})[column] = anchor
        _save_table_settings(name, entry)

    def reset():
        _save_table_settings(name, None)
        for column in columns:
            if column not in tree._tools["skip"]:
                tree.column(column, anchor=default_anchor(tree._tools["labels"][column]))
        sort_table(tree, None)

    def header_menu(event):
        if tree.identify_region(event.x, event.y) not in ("heading", "separator"):
            return None
        column = column_at(event.x)
        if column is None or column in tree._tools["skip"]:
            return None
        menu = tk.Menu(tree, tearoff=False)
        label = tree._tools["labels"][column]
        menu.add_command(label=f"‘{label}’ 오름차순 정렬", command=lambda: sort_table(tree, column, descending=False))
        menu.add_command(label=f"‘{label}’ 내림차순 정렬", command=lambda: sort_table(tree, column, descending=True))
        menu.add_command(label="원래 순서로", command=lambda: sort_table(tree, None))
        menu.add_separator()
        for text, anchor in (("왼쪽 맞춤", "w"), ("가운데 맞춤", "center"), ("오른쪽 맞춤", "e")):
            menu.add_command(label=("✓ " if str(tree.column(column, "anchor")) == anchor else "   ") + text,
                             command=lambda a=anchor: set_anchor(column, a))
        menu.add_separator()
        menu.add_command(label="이 표의 칸 너비·맞춤 처음대로", command=reset)
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    tree.bind("<ButtonPress-1>", press, add="+")
    tree.bind("<ButtonRelease-1>", release, add="+")
    tree.bind("<Button-3>", header_menu, add="+")
    return tree


def sort_table(tree, column, toggle=False, descending=False):
    """Sort the rows by a column (None: back to the order the screen filled in)."""
    tools = getattr(tree, "_tools", None)
    if tools is None:
        return
    current = tools["sort"]
    if toggle:
        if current and current[0] == column:
            column, descending = (column, True) if not current[1] else (None, False)
        else:
            descending = False
    tools["sort"] = (column, descending) if column else None
    for name, label in tools["labels"].items():
        if name not in tools["skip"]:
            arrow = ARROWS[1 if descending else 0] if name == column else ""
            tree.heading(name, text=label + arrow)
    _apply_sort(tree)


def resort(tree):
    """Call after a screen refilled the table: remember the fill order, then apply the user's sort."""
    tools = getattr(tree, "_tools", None)
    if tools:
        tools["fill_order"] = list(tree.get_children())
        _apply_sort(tree)


def _apply_sort(tree):
    tools = tree._tools
    rows = list(tree.get_children())
    if not tools.get("fill_order") or set(tools["fill_order"]) != set(rows):
        tools["fill_order"] = rows if tools["sort"] else None
    if tools["sort"] is None:
        rows = tools.get("fill_order") or rows
    else:
        column, descending = tools["sort"]
        rows = sorted(rows, key=lambda row: sort_value(tree.set(row, column)), reverse=descending)
    for index, row in enumerate(rows):
        tree.move(row, "", index)
