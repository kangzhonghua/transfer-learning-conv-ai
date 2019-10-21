import glob
import csv
import os
import time
import json

start = time.time()

base_dir = "/home/Public/data/transfer-learning/output/output-qa/xinli001_jiandanxinli-qa"
output_dir = "/home/Public/data/transfer-learning/output/output-qa/xinli001_jiandanxinli-convai"

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

file_list = glob.glob(os.path.join(base_dir, "*"))

# print(file_list)

personality = ["我是一名心理咨询师。"]

for file_name in file_list[0:1]:
    data_list = []
    print(file_name)
    with open(file_name, "r", encoding='utf8') as input_file:
        for line in input_file:

            line_json = json.loads(line)

            text = "".join(text for text in line_json["text"]) if (len(line_json["text"]) > 0) else ""
            title = "".join(text for text in line_json["title"]) if (len(line_json["title"]) > 0) else ""

            # print(f"{text},{title}\n")

            for answer in line_json["answers"]:

                history = []
                if (text.find(title) > 0):
                    history.append(f"{title}。{text}")
                else:
                    history.append(text)

                data_dict = {}
                data_dict["personality"] = personality

                # print(answer)
                answer_size = len(answer)

                utterances = []
                for i in range(0, answer_size, 2):

                    candidates = []

                    sub_sentence = answer[i]
                    candidates.append(sub_sentence)

                    utterance = {}

                    utterance["candidates"] = candidates
                    utterance["history"] = history.copy()

                    utterances.append(utterance)

                    if (answer_size > i + 1):
                        history.append(sub_sentence)
                        history.append(answer[i + 1])

                data_dict["utterances"] = utterances
                data_list.append(data_dict)

    path, short_name = os.path.split(file_name)
    name, ext = os.path.splitext(short_name)

    with open(os.path.join(output_dir, f"{name}convai.json"), "w", encoding='utf8') as output_file:
        output_file.write(json.dumps(data_list, ensure_ascii=False, indent=4, separators=(',', ': ')))

end = time.time()

print(f"end in second {end - start}.")
