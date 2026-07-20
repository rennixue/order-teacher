from typing import TypedDict


class ProfessionDict(TypedDict):
    id: int
    en_name: str
    zh_name: str


# 2,4,7,8,11,12,15,17,18,20,24,25,26,33,36,38,39,42,43,45,46,47,48,49,50,51,53,55,59,60,62,63,67,69,86,87,89,91,92,93,95,96,97,99,100,101,102,103,106,107,121,122
PROFESSIONS: list[ProfessionDict] = [
    {"id": 2, "en_name": "Communications", "zh_name": "传播学"},
    {"id": 4, "en_name": "Earth Science", "zh_name": "地球科学"},
    {"id": 7, "en_name": "Law", "zh_name": "法学"},
    {"id": 8, "en_name": "Industrial Engineering", "zh_name": "工业工程"},
    {"id": 11, "en_name": "Management", "zh_name": "管理学"},
    {"id": 12, "en_name": "Advertising", "zh_name": "广告学"},
    {"id": 15, "en_name": "Chemistry", "zh_name": "化学"},
    {"id": 17, "en_name": "Accounting", "zh_name": "会计学"},
    {"id": 18, "en_name": "Mechanical Engineering", "zh_name": "机械工程"},
    {"id": 20, "en_name": "Computer Science", "zh_name": "计算机科学"},
    {"id": 24, "en_name": "Finance", "zh_name": "金融学"},
    {"id": 25, "en_name": "Economics", "zh_name": "经济学"},
    {"id": 26, "en_name": "Actuarial Science", "zh_name": "精算学"},
    {"id": 33, "en_name": "Sociology / Social Science", "zh_name": "社会学"},
    {"id": 36, "en_name": "Biology", "zh_name": "生物学"},
    {"id": 38, "en_name": "Food Science", "zh_name": "食品科学"},
    {"id": 39, "en_name": "Marketing", "zh_name": "市场营销"},
    {"id": 42, "en_name": "Mathematics", "zh_name": "数学"},
    {"id": 43, "en_name": "Media", "zh_name": "数字媒体"},
    {"id": 45, "en_name": "Statistics", "zh_name": "统计学"},
    {"id": 46, "en_name": "Civil and Building Engineering", "zh_name": "土木工程"},
    {"id": 47, "en_name": "Physics", "zh_name": "物理学"},
    {"id": 48, "en_name": "Logistics / Supply Chain Management", "zh_name": "物流学 / 供应链管理"},
    {"id": 49, "en_name": "Psychology", "zh_name": "心理学"},
    {"id": 50, "en_name": "Journalism", "zh_name": "新闻学"},
    {"id": 51, "en_name": "Information Technology", "zh_name": "信息技术"},
    {"id": 53, "en_name": "Political Science / Government", "zh_name": "政治学"},
    {"id": 55, "en_name": "Environment Science", "zh_name": "环境科学"},
    {"id": 59, "en_name": "Logic", "zh_name": "逻辑学"},
    {"id": 60, "en_name": "Pedagogy", "zh_name": "教育学"},
    {"id": 62, "en_name": "Linguistics", "zh_name": "语言学"},
    {"id": 63, "en_name": "Architecture", "zh_name": "建筑学"},
    {"id": 67, "en_name": "International Business", "zh_name": "国际商务"},
    {"id": 69, "en_name": "Electrical Engineering", "zh_name": "电气工程"},
    {"id": 86, "en_name": "Other Enginnering", "zh_name": "其他工程"},
    {"id": 87, "en_name": "Materials Science and Engineering", "zh_name": "材料科学与工程"},
    {"id": 89, "en_name": "Control Science and Engineering", "zh_name": "控制科学与工程"},
    {"id": 91, "en_name": "Mechanics", "zh_name": "力学"},
    {"id": 92, "en_name": "Banking", "zh_name": "银行学"},
    {"id": 93, "en_name": "Insurance", "zh_name": "保险学"},
    {"id": 95, "en_name": "Medicine", "zh_name": "医学"},
    {"id": 96, "en_name": "Financial Mathematics", "zh_name": "金融数学"},
    {"id": 97, "en_name": "Electrical and Electronic Engineering", "zh_name": "电子电气工程"},
    {"id": 99, "en_name": "Art Theory and Design Science", "zh_name": "艺术学与设计学"},
    {"id": 100, "en_name": "Communication Engineering", "zh_name": "通信工程"},
    {"id": 101, "en_name": "Financial Engineering", "zh_name": "金融工程"},
    {"id": 102, "en_name": "Project Management", "zh_name": "项目管理"},
    {"id": 103, "en_name": "English Writing", "zh_name": "英语写作"},
    {"id": 106, "en_name": "Fashion Design", "zh_name": "服装设计"},
    {"id": 107, "en_name": "Business Analytics", "zh_name": "商业分析"},
    {"id": 121, "en_name": "Biomedical Engineering", "zh_name": "生物医学工程"},
    {"id": 122, "en_name": "History", "zh_name": "历史学"},
]


ORDER_TYPES = [
    {"id": 0, "name": "定制辅导"},
    {"id": 1, "name": "考前突击"},
    {"id": 26, "name": "包课辅导"},
    {"id": 27, "name": "论文润色"},
    {"id": 64, "name": "班课辅导"},
    {"id": 65, "name": "论文大礼包"},
    {"id": 66, "name": "特殊订单"},
    {"id": 67, "name": "毕业大论文"},
    {"id": 69, "name": "文案类"},
    {"id": 70, "name": "实习类"},
    {"id": 71, "name": "作业辅导"},
    {"id": 72, "name": "Course Package"},
]
