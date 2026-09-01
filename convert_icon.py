from PIL import Image
img = Image.open("icon.png")
img.save("icon.ico", format="ICO", sizes=[(16,16), (32,32), (48,48), (256,256)])
print("转换完成：icon.ico")