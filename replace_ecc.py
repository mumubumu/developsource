import os  
import sys  

# 检查参数个数  
if len(sys.argv) != 3:  
    print(f"用法: {sys.argv[0]} <input_file> <n|y>")  
    print(f"其中 <n|y> 表示要将 ECC=y 替换为 ECC=n 还是将 ECC=n 替换为 ECC=y。")  
    sys.exit(1)  

input_file_path = sys.argv[1]  
action = sys.argv[2].lower()  

# 检查输入文件是否存在  
if not os.path.exists(input_file_path):  
    print(f"错误：输入文件 {input_file_path} 不存在。")  
    sys.exit(1)  

# 检查动作参数是否有效  
if action not in ['n', 'y']:  
    print(f"错误：无效的动作参数 {action}。请使用 'n' 或 'y'。")  
    sys.exit(1)  

# 定义替换逻辑  
replace_dict = {'n': {'ENABLE_DRAM_ECC := y': 'ENABLE_DRAM_ECC := n'}, 'y': {'ENABLE_DRAM_ECC := n': 'ENABLE_DRAM_ECC := y'}}  
replacement_pairs = replace_dict[action]  

# 尝试读取输入文件并替换内容  
try:  
    with open(input_file_path, 'r') as input_file:  
        content = input_file.read()  
        new_content = content  
        for old, new in replacement_pairs.items():  
            new_content = new_content.replace(old, new)  

    # 如果替换成功，将内容写回原文件（如果需要可以指定新的输出文件）  
    with open(input_file_path, 'w') as output_file:  
        output_file.write(new_content)  

    print(f'替换完成，结果已写回{input_file_path}')  

except IOError as e:  
    print(f"处理文件时发生错误：{e}")  
    sys.exit(1)




