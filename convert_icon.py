from PIL import Image

try:
    im = Image.open('Logo.png')
    im.save('Logo.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    print('Logo.ico created.')
except Exception as e:
    print('Error converting Logo.png to Logo.ico:', e)
