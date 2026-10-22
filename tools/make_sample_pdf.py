# -*- coding: utf-8 -*-
"""生成测试用样例作文 PDF（3 名学生），用于体验"上传 → 核对 → 批改"全流程。
用法: python tools/make_sample_pdf.py   ->  生成 样例作文.pdf（在项目根目录）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    font_path = r"C:\Windows\Fonts\msyh.ttc"
    if not os.path.exists(font_path):
        font_path = r"C:\Windows\Fonts\simsun.ttc"
    pdfmetrics.registerFont(TTFont("CJK", font_path, subfontIndex=0))
    st = ParagraphStyle("body", fontName="CJK", fontSize=11, leading=19, wordWrap="CJK")

    essays = [
        ("张三", "我的家乡",
         "我的家乡是一个美丽的小镇，坐落在青山绿水之间。春天，漫山遍野的桃花开了，像一片粉色的海洋。夏天，孩子们在小河里捉鱼摸虾，欢声笑语传遍整个村庄。秋天，田野里金黄的稻浪随风起伏，大人们忙着收割，脸上洋溢着丰收的喜悦。冬天，雪花飘落，整个小镇银装素裹，安静又美好。"
         "我爱我的家乡，不仅因为它风景如画，更因为那里有我最亲的家人和最快乐的童年。无论我走到哪里，家乡永远是我心中最温暖的港湾。"
         "通过这篇作文，我明白了：真正打动人的，不是华丽的辞藻，而是真挚的情感。今后我要多观察生活，把家乡最美的瞬间写下来。"),
        ("李四", "我的老师",
         "我的语文老师姓王，她有一双会说话的眼睛。记得有一次，我在课堂上走神了，王老师没有当众批评我，只是用温柔的目光看了我一眼，我立刻坐直了身子。课后，她还把我叫到办公室，耐心地询问我是不是遇到了什么困难。"
         "王老师讲课特别生动。讲古诗词的时候，她总会把诗人背后的故事讲给我们听，仿佛让我们穿越到了古代。在她的课上，原本枯燥的课文变得妙趣横生。"
         "老师就像一盏明灯，照亮了我们前进的道路。我想对王老师说一声：谢谢您，您辛苦了！以后我也要成为像您一样温暖的人。"),
        ("王五", "难忘的一次比赛",
         "上周五，学校举行了秋季运动会，我参加了八百米长跑比赛。赛前，我紧张得手心冒汗，生怕自己跑不好给班级丢脸。"
         "随着发令枪响，我像离弦的箭一样冲了出去。可是跑到一半的时候，我的腿像灌了铅一样沉重，呼吸也越来越急促。就在我想要放弃的时候，我听见了同学们震耳欲聋的加油声：「加油！你可以的！」那一刻，我咬紧牙关，拼尽全力冲向终点。"
         "虽然最后我只获得了第四名，但我战胜了自己，懂得了坚持的意义。这次比赛让我明白：人生就像长跑，最重要的不是名次，而是永不言弃的精神。"),
    ]

    path = os.path.join(BASE, "样例作文.pdf")
    doc = SimpleDocTemplate(path, pagesize=A4, topMargin=60, bottomMargin=60)
    story = []
    from reportlab.platypus import PageBreak
    for i, (name, title, body) in enumerate(essays):
        story.append(Paragraph(name, st))
        story.append(Paragraph(title, st))
        story.append(Paragraph(body, st))
        if i < len(essays) - 1:
            story.append(PageBreak())  # 每名学生一页，模拟真实收集的作文 PDF
    doc.build(story)
    print(f"已生成样例 PDF: {path}")


if __name__ == "__main__":
    main()
