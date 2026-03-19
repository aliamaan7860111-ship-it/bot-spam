import os
from google import genai

client = genai.Client(api_key='AIzaSyCIwq9uMdxfEx3zEXcSk_an_XmMD1qOI9o')

def test_nano_banana():
    try:
        print("Connecting to Nano Banana 2...")
        result = client.models.generate_images(
            model='imagen-4.0-generate-001',
            prompt='An apple sitting on a table',
            config=genai.types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="3:4",
                output_mime_type="image/jpeg"
            )
        )
        print("Success! Image generated:")
        img = result.generated_images[0]
        # Check what the actual image object looks like natively 
        print(dir(img))
        if hasattr(img, 'image'):
            img_obj = img.image
            print("Image bytes length:", len(img_obj.image_bytes))
            with open('test_apple.jpg', 'wb') as f:
                f.write(img_obj.image_bytes)
            print("Successfully saved test_apple.jpg")
            
    except Exception as e:
        print("Crash:", e)

if __name__ == "__main__":
    test_nano_banana()
